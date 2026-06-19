// ============================================================
// SEAS5 × ERA5-Land — pixel-level Spearman correlation
// Paste into GEE Code Editor: code.earthengine.google.com
//
// Filters using stored properties (leadtime, system:time_start)
// rather than computed month/year properties — keeps the
// computation graph shallow and fast.
//
// ERA5-Land (ECMWF/ERA5_LAND/MONTHLY_AGGR) is land-only.
// ============================================================

// ── raw collections ───────────────────────────────────────────

var SEAS5_IC = ee.ImageCollection('projects/ee-zackarno/assets/seas5_monthly')
  .filter(ee.Filter.notNull(['date_issued', 'date_valid', 'leadtime']));

var ERA5_IC = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR')
  .select('total_precipitation_sum');

// ASAP precipitation-sensitive period (PSP) monthly bitmasks at admin1.
// 48 bands = {crop, rangeland} × {season-1, season-2} × {Jan..Dec}.
// Ingestion stripped GeoTIFF band descriptions, so we rename on load.
var PSP_BAND_NAMES = (function() {
  var n = [];
  ['crop', 'range'].forEach(function(lu) {
    ['s1', 's2'].forEach(function(s) {
      for (var m = 1; m <= 12; m++) {
        n.push(lu + '_' + s + '_m' + (m < 10 ? '0' : '') + m);
      }
    });
  });
  return n;
})();
var PSP_MASK = ee.Image('projects/ee-zackarno/assets/asap_psp_adm1_mask')
  .rename(PSP_BAND_NAMES);

// ── seasons ───────────────────────────────────────────────────

// All 12 rolling 3-month trimesters in calendar order.
// Year-offset handling is computed automatically via expandSeason().
var SEASONS = {
  'JFM (Jan-Feb-Mar)': { months: [1, 2, 3]   },
  'FMA (Feb-Mar-Apr)': { months: [2, 3, 4]   },
  'MAM (Mar-Apr-May)': { months: [3, 4, 5]   },
  'AMJ (Apr-May-Jun)': { months: [4, 5, 6]   },
  'MJJ (May-Jun-Jul)': { months: [5, 6, 7]   },
  'JJA (Jun-Jul-Aug)': { months: [6, 7, 8]   },
  'JAS (Jul-Aug-Sep)': { months: [7, 8, 9]   },
  'ASO (Aug-Sep-Oct)': { months: [8, 9, 10]  },
  'SON (Sep-Oct-Nov)': { months: [9, 10, 11] },
  'OND (Oct-Nov-Dec)': { months: [10, 11, 12]},
  'NDJ (Nov-Dec-Jan)': { months: [11, 12, 1] },
  'DJF (Dec-Jan-Feb)': { months: [12, 1, 2]  },
};

var MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun',
                   'Jul','Aug','Sep','Oct','Nov','Dec'];

var CORR_VIZ = {
  min: -1, max: 1,
  palette: ['#d73027','#f46d43','#fdae61','#fee08b','#ffffbf',
            '#d9ef8b','#a6d96a','#66bd63','#1a9850']
};

// percentile of forecast vs historical: dry (low) → red, wet (high) → blue
var PCT_VIZ = {
  min: 0, max: 100,
  palette: ['#a50026','#d73027','#f46d43','#fdae61','#fee090',
            '#ffffbf','#e0f3f8','#abd9e9','#74add1','#4575b4','#313695']
};
// return periods (years) — log-ish single-hue scales
var RP_WET_VIZ = {
  min: 1, max: 30,
  palette: ['#f7fbff','#deebf7','#9ecae1','#4292c6','#2171b5','#08519c','#08306b']
};
var RP_DRY_VIZ = {
  min: 1, max: 30,
  palette: ['#fff5f0','#fee0d2','#fc9272','#ef3b2c','#cb181d','#a50f15','#67000d']
};

// ── helpers ───────────────────────────────────────────────────

function getLeadtimes(issuedMonth, months) {
  return months.map(function(vm) { return (vm - issuedMonth + 12) % 12; });
}

// For a season's months list, return [{m, dy}] where dy is year offset
// relative to the season's "label year" (= year of the last month).
// e.g. DJF [12,1,2] -> [{12,-1}, {1,0}, {2,0}] ;  NDJ [11,12,1] -> [{11,-1},{12,-1},{1,0}]
function expandSeason(months) {
  var lastM = months[months.length - 1];
  return months.map(function(m) {
    return { m: m, dy: m > lastM ? -1 : 0 };
  });
}

// In-season binary mask for a given trimester + land-use selection.
//   months:      e.g. [6, 7, 8] for JJA
//   landUseMode: 'crop', 'range', or 'either'
// Result is 1 wherever the admin1's PSP for the chosen layer(s) overlaps
// any of the trimester's months; admins outside ASAP coverage (or whose
// PSP doesn't intersect) are 0.
function buildPspMask(months, landUseMode) {
  var prefixes = [];
  if (landUseMode === 'crop'  || landUseMode === 'either') prefixes.push('crop_s1_',  'crop_s2_');
  if (landUseMode === 'range' || landUseMode === 'either') prefixes.push('range_s1_', 'range_s2_');
  var bands = [];
  months.forEach(function(m) {
    var mm = (m < 10 ? '0' : '') + m;
    prefixes.forEach(function(p) { bands.push(p + 'm' + mm); });
  });
  return PSP_MASK.select(bands).reduce(ee.Reducer.max()).gt(0);
}

// Build an ee.Filter that selects images valid in this season for label year y.
function buildSeasonFilter(expanded, y) {
  var subs = expanded.map(function(em) {
    return ee.Filter.and(
      ee.Filter.calendarRange(em.m, em.m, 'month'),
      ee.Filter.calendarRange(y.add(em.dy), y.add(em.dy), 'year')
    );
  });
  return subs.length === 1 ? subs[0] : ee.Filter.or.apply(null, subs);
}

// ── computation ───────────────────────────────────────────────
// system:time_start on SEAS5 = valid date, so calendarRange filters on valid date.
// For DJF year Y: Dec is in year Y-1, Jan+Feb in year Y.

function computeCorrelation(issuedMonth, seasonKey, startYear, endYear) {
  var months   = SEASONS[seasonKey].months;
  var expanded = expandSeason(months);
  var lts      = getLeadtimes(issuedMonth, months);
  var ltStrs   = lts.map(function(lt) { return String(lt); });
  var years    = ee.List.sequence(startYear, endYear);

  var imPad = (issuedMonth < 10 ? '0' : '') + issuedMonth;
  var imSub = '-' + imPad + '-';

  // Pre-filter SEAS5 by leadtime AND issued month (stored string properties)
  var seas5Base = SEAS5_IC
    .filter(ee.Filter.inList('leadtime', ltStrs))
    .filter(ee.Filter.stringContains('date_issued', imSub))
    .select('precip');

  var paired = ee.ImageCollection(years.map(function(y) {
    y = ee.Number(y);
    var seasonFilter = buildSeasonFilter(expanded, y);
    var s = seas5Base.filter(seasonFilter).mean().rename('seas5');
    var e = ERA5_IC.filter(seasonFilter).mean().rename('era5');
    return s.addBands(e).set('year', y);
  }));

  return paired.reduce(ee.Reducer.spearmansCorrelation());
}

// Forecast anomaly: percentile rank + return periods of forecastYear's seasonal
// mean against the historical climatology (years in [startYear, endYear] minus
// forecastYear, leave-one-out). Returns an Image with bands pct, rp_wet, rp_dry.
//
// Weibull-style: K = count(historical <= current), N = number of historical years.
//   pct    = 100 * (K+1) / (N+1)
//   rp_wet = (N+1) / max(N - K, 1)        // bounded [~1, N+1]
//   rp_dry = (N+1) / (K + 1)              // bounded [1, N+1]
function computeAnomaly(issuedMonth, seasonKey, startYear, endYear, forecastYear) {
  var months   = SEASONS[seasonKey].months;
  var expanded = expandSeason(months);
  var lts      = getLeadtimes(issuedMonth, months);
  var ltStrs   = lts.map(function(lt) { return String(lt); });

  var imPad = (issuedMonth < 10 ? '0' : '') + issuedMonth;
  var imSub = '-' + imPad + '-';

  var seas5Base = SEAS5_IC
    .filter(ee.Filter.inList('leadtime', ltStrs))
    .filter(ee.Filter.stringContains('date_issued', imSub))
    .select('precip');

  // climatology = year range minus forecastYear (leave-one-out)
  var clmYears = [];
  for (var y = startYear; y <= endYear; y++) {
    if (y !== forecastYear) clmYears.push(y);
  }

  var historical = ee.ImageCollection(clmYears.map(function(y) {
    var yNum = ee.Number(y);
    return seas5Base.filter(buildSeasonFilter(expanded, yNum)).mean().set('year', y);
  }));

  var current = seas5Base
    .filter(buildSeasonFilter(expanded, ee.Number(forecastYear)))
    .mean();

  var N = ee.Number(historical.size());
  var K = historical.map(function(h) { return h.lte(current); }).sum();

  var pct = K.add(1).divide(N.add(1)).multiply(100).rename('pct');
  var rpW = ee.Image(N.add(1))
    .divide(K.subtract(N).multiply(-1).max(1))
    .rename('rp_wet');
  var rpD = ee.Image(N.add(1)).divide(K.add(1)).rename('rp_dry');

  return ee.Image.cat([pct, rpW, rpD])
    .set('N', N, 'forecast_year', forecastYear);
}

// ── UI ────────────────────────────────────────────────────────

Map.setCenter(20, 10, 2);
Map.setOptions('TERRAIN');
Map.style().set('cursor', 'crosshair');

var panel = ui.Panel({
  style: { width: '300px', padding: '12px', position: 'top-left',
           backgroundColor: 'rgba(255,255,255,0.95)' }
});
ui.root.add(panel);

panel.add(ui.Label('SEAS5 Visualizer', {
  fontWeight: 'bold', fontSize: '15px', margin: '0 0 6px 0'
}));

// Style helpers for sectioned layout — tightened to keep sidebar compact
var SECTION_PANEL_STYLE = {
  border: '1px solid #888', padding: '6px 8px', margin: '4px 0', stretch: 'horizontal'
};
var SECTION_HEADER_STYLE = {
  fontWeight: 'bold', fontSize: '12px', color: '#333',
  margin: '0 0 4px 0', stretch: 'horizontal'
};
var INPUT_LABEL_STYLE = { fontWeight: 'bold', fontSize: '11px', margin: '4px 0 2px 0' };
var COMPUTE_BTN_STYLE = {
  color: 'red', fontWeight: 'bold',
  stretch: 'horizontal', margin: '4px 0 2px 0'
};

// ── 1. GENERAL ────────────────────────────────────────────────
var generalPanel = ui.Panel({ style: SECTION_PANEL_STYLE });
panel.add(generalPanel);
generalPanel.add(ui.Label('1. GENERAL', SECTION_HEADER_STYLE));

generalPanel.add(ui.Label('Issued month', INPUT_LABEL_STYLE));
var issuedSelect = ui.Select({ items: MONTH_NAMES, value: 'May', style: { stretch: 'horizontal' } });
generalPanel.add(issuedSelect);

generalPanel.add(ui.Label('Valid season', INPUT_LABEL_STYLE));
var seasonSelect = ui.Select({
  items: Object.keys(SEASONS), value: 'JJA (Jun-Jul-Aug)', style: { stretch: 'horizontal' }
});
generalPanel.add(seasonSelect);

var ltLabel = ui.Label('', { fontSize: '10px', color: '#555', margin: '2px 0 0 0' });
generalPanel.add(ltLabel);

function updateLtLabel() {
  var issuedMonth = MONTH_NAMES.indexOf(issuedSelect.getValue()) + 1;
  var months = SEASONS[seasonSelect.getValue()].months;
  var lts = getLeadtimes(issuedMonth, months);
  var bad = lts.filter(function(lt) { return lt > 6; });
  if (bad.length > 0) {
    ltLabel.setValue('⚠ lt=' + lts.join(',') + ' — some > 6, no SEAS5 data.');
    ltLabel.style().set('color', '#c00');
  } else {
    ltLabel.setValue('Leadtimes: lt=' + lts.join(', '));
    ltLabel.style().set('color', '#555');
  }
}
function markStale() {
  if (lastResult)     statusLabel.setValue('Selection changed — click Compute skill to update.');
  if (lastAnomResult) anomStatusLabel.setValue('Selection changed — click Compute anomaly to update.');
}
function markStaleAnom() {
  if (lastAnomResult) anomStatusLabel.setValue('Selection changed — click Compute anomaly to update.');
}

issuedSelect.onChange(function() { updateLtLabel(); markStale(); });
seasonSelect.onChange(function() { updateLtLabel(); markStale(); });
updateLtLabel();

generalPanel.add(ui.Label('Year range', INPUT_LABEL_STYLE));
var yearRow = ui.Panel({ layout: ui.Panel.Layout.flow('horizontal'), style: { stretch: 'horizontal' } });
var yearItems = [];
for (var y = 1981; y <= 2026; y++) yearItems.push(String(y));
var LATEST_YEAR = String(2026);
var startYearSelect = ui.Select({ items: yearItems, value: '1993', style: { stretch: 'horizontal' },
  onChange: function() { markStale(); } });
var endYearSelect   = ui.Select({ items: yearItems, value: '2025', style: { stretch: 'horizontal' },
  onChange: function() { markStale(); } });
yearRow.add(startYearSelect);
yearRow.add(ui.Label('–', { margin: '5px 4px' }));
yearRow.add(endYearSelect);
generalPanel.add(yearRow);

// ── 2. SKILL ──────────────────────────────────────────────────
var skillPanel = ui.Panel({ style: SECTION_PANEL_STYLE });
panel.add(skillPanel);
skillPanel.add(ui.Label('2. SKILL', SECTION_HEADER_STYLE));

// Skill filter: optional, positive-only (r >= threshold).
var filterSkillCheck = ui.Checkbox({
  label: 'Filter to positive skill (r ≥ threshold)', value: false,
  style: { margin: '4px 0 2px 0', fontSize: '11px' }
});
skillPanel.add(filterSkillCheck);

var rFilterPanel = ui.Panel({
  style: { stretch: 'horizontal', shown: false, margin: '0', padding: '0' }
});
var rSlider = ui.Slider({
  min: 0, max: 0.8, value: 0.35, step: 0.05,
  style: { stretch: 'horizontal' }
});
rFilterPanel.add(rSlider);
rFilterPanel.add(ui.Label(
  'n=32: r≥0.35 ≈ p<0.05; r≥0.45 ≈ p<0.01.',
  { fontSize: '9px', color: '#777', margin: '0' }
));
skillPanel.add(rFilterPanel);

filterSkillCheck.onChange(function(checked) {
  rFilterPanel.style().set('shown', checked);
  clipAnomCheck.style().set('shown', checked);
  if (!checked) clipAnomCheck.setValue(false);  // reset when filter turned off
  renderLayers();
});

var computeBtn = ui.Button({ label: 'Compute skill', style: COMPUTE_BTN_STYLE });
skillPanel.add(computeBtn);

var statusLabel = ui.Label('', { fontSize: '10px', color: '#555', margin: '2px 0' });
skillPanel.add(statusLabel);

// Skill legend (Spearman r colorbar)
skillPanel.add(ui.Label('Spearman r', { fontWeight: 'bold', fontSize: '11px', margin: '6px 0 2px 0' }));
var legendRow = ui.Panel({
  layout: ui.Panel.Layout.flow('horizontal'),
  style: { stretch: 'horizontal', margin: '0', padding: '0' }
});
CORR_VIZ.palette.forEach(function(c) {
  legendRow.add(ui.Label('', {
    backgroundColor: c, padding: '3px 0', margin: '0', stretch: 'horizontal'
  }));
});
skillPanel.add(legendRow);
skillPanel.add(ui.Panel({
  widgets: [
    ui.Label('-1', { fontSize: '9px', stretch: 'horizontal', textAlign: 'left',   margin: '0' }),
    ui.Label('0',  { fontSize: '9px', stretch: 'horizontal', textAlign: 'center', margin: '0' }),
    ui.Label('+1', { fontSize: '9px', stretch: 'horizontal', textAlign: 'right',  margin: '0' }),
  ],
  layout: ui.Panel.Layout.flow('horizontal'),
  style: { stretch: 'horizontal' }
}));

// ── 3. ANOMALY ────────────────────────────────────────────────
var anomalyPanel = ui.Panel({ style: SECTION_PANEL_STYLE });
panel.add(anomalyPanel);
anomalyPanel.add(ui.Label('3. ANOMALY', SECTION_HEADER_STYLE));
anomalyPanel.add(ui.Label('Year range = climatology; forecast year auto-excluded.',
  { fontSize: '10px', color: '#777', margin: '0 0 2px 0' }));

anomalyPanel.add(ui.Label('Forecast year', INPUT_LABEL_STYLE));
var forecastYearSelect = ui.Select({
  items: yearItems, value: LATEST_YEAR, style: { stretch: 'horizontal' },
  onChange: function() { markStaleAnom(); }
});
anomalyPanel.add(forecastYearSelect);

var computeAnomBtn = ui.Button({ label: 'Compute anomaly', style: COMPUTE_BTN_STYLE });
anomalyPanel.add(computeAnomBtn);

var anomStatusLabel = ui.Label('', { fontSize: '10px', color: '#555', margin: '2px 0' });
anomalyPanel.add(anomStatusLabel);

// Optional: clip anomaly to skill mask. Only visible when the skill filter is on.
var clipAnomCheck = ui.Checkbox({
  label: 'Clip anomalies to skill mask', value: false,
  style: { margin: '2px 0 0 0', fontSize: '11px', shown: false }
});
anomalyPanel.add(clipAnomCheck);
clipAnomCheck.onChange(function() { renderLayers(); });

// Anomaly legends — three stacked, one per layer (compact)
function addAnomLegend(title, palette, lo, mid, hi) {
  anomalyPanel.add(ui.Label(title, { fontWeight: 'bold', fontSize: '10px', margin: '4px 0 1px 0' }));
  var row = ui.Panel({
    layout: ui.Panel.Layout.flow('horizontal'),
    style: { stretch: 'horizontal', margin: '0' }
  });
  palette.forEach(function(c) {
    row.add(ui.Label('', { backgroundColor: c, padding: '3px 0', margin: '0', stretch: 'horizontal' }));
  });
  anomalyPanel.add(row);
  anomalyPanel.add(ui.Panel({
    widgets: [
      ui.Label(lo,  { fontSize: '9px', stretch: 'horizontal', textAlign: 'left',   margin: '0' }),
      ui.Label(mid, { fontSize: '9px', stretch: 'horizontal', textAlign: 'center', margin: '0' }),
      ui.Label(hi,  { fontSize: '9px', stretch: 'horizontal', textAlign: 'right',  margin: '0' }),
    ],
    layout: ui.Panel.Layout.flow('horizontal'),
    style: { stretch: 'horizontal' }
  }));
}
addAnomLegend('Percentile (dry → wet)', PCT_VIZ.palette, '0', '50', '100');
addAnomLegend('Wet RP (years)',         RP_WET_VIZ.palette, '1', '15', '30+');
addAnomLegend('Dry RP (years)',         RP_DRY_VIZ.palette, '1', '15', '30+');

// ── 4. IN-SEASON MASK ─────────────────────────────────────────
// Applies an admin1-level mask: keep only pixels whose admin's ASAP
// precipitation-sensitive period overlaps the selected valid season.
var pspPanel = ui.Panel({ style: SECTION_PANEL_STYLE });
panel.add(pspPanel);
pspPanel.add(ui.Label('4. IN-SEASON MASK', SECTION_HEADER_STYLE));

var pspCheck = ui.Checkbox({
  label: 'Mask to in-season admins', value: false,
  style: { margin: '4px 0 2px 0', fontSize: '11px' }
});
pspPanel.add(pspCheck);

var pspModePanel = ui.Panel({
  style: { stretch: 'horizontal', shown: false, margin: '0', padding: '0' }
});
pspModePanel.add(ui.Label('Land use', INPUT_LABEL_STYLE));
var pspModeSelect = ui.Select({
  items: [
    { label: 'crop only',      value: 'crop' },
    { label: 'rangeland only', value: 'range' },
    { label: 'either',         value: 'either' },
  ],
  value: 'either',
  style: { stretch: 'horizontal' }
});
pspModePanel.add(pspModeSelect);
pspModePanel.add(ui.Label(
  'Mask = 1 wherever admin\'s PSP for the selected layer(s) overlaps any month in the valid season.',
  { fontSize: '9px', color: '#777', margin: '2px 0 0 0' }
));
pspPanel.add(pspModePanel);

pspCheck.onChange(function(checked) {
  pspModePanel.style().set('shown', checked);
  renderLayers();
});
pspModeSelect.onChange(renderLayers);

// click-to-inspect (lives on the main panel below all sections)
var inspectLabel = ui.Label('Click map to inspect pixel values.', {
  fontSize: '10px', color: '#777', margin: '6px 0 0 0'
});
panel.add(inspectLabel);

var lastResult     = null;
var lastLabel      = '';
var lastAnomResult = null;
var lastAnomLabel  = '';

// FAO simplified admin-1 boundaries — outline only, loaded once at app start.
// Kept on top of the layer stack after each render so boundaries are always visible.
var FAO_ADM1   = ee.FeatureCollection('FAO/GAUL_SIMPLIFIED_500m/2015/level1');
var fao_styled = FAO_ADM1.style({ color: '666666', fillColor: '00000000', width: 0.6 });
var faoLayer   = Map.addLayer(fao_styled, {}, 'FAO admin 1', true);

// Track skill/anomaly layers so renderLayers can replace them without
// re-creating the FAO layer.
var managedLayers = [];

function renderLayers() {
  // Remove only the data layers we previously added (FAO untouched)
  managedLayers.forEach(function(l) { Map.layers().remove(l); });
  managedLayers = [];

  function addManaged(img, viz, name, shown) {
    var layer = Map.addLayer(img, viz, name, shown);
    managedLayers.push(layer);
  }

  // Optional admin-1 in-season mask, applies to both skill and anomaly layers.
  var pspMask   = null;
  var pspSuffix = '';
  if (pspCheck.getValue()) {
    var months  = SEASONS[seasonSelect.getValue()].months;
    var pspMode = pspModeSelect.getValue();
    pspMask   = buildPspMask(months, pspMode);
    pspSuffix = ' [in-season: ' + pspMode + ']';
  }

  // Skill layer
  if (lastResult) {
    var corr     = lastResult.select('correlation');
    var doFilter = filterSkillCheck.getValue();
    var display, suffix;
    if (doFilter) {
      var t = rSlider.getValue();
      display = corr.updateMask(corr.gte(t));   // negatives always excluded
      suffix  = ' (r≥' + t.toFixed(2) + ')';
    } else {
      display = corr;
      suffix  = '';
    }
    if (pspMask) display = display.updateMask(pspMask);
    addManaged(display, CORR_VIZ, 'r — ' + lastLabel + suffix + pspSuffix, true);
  }

  // All three anomaly layers — Percentile shown by default, RPs hidden but toggleable.
  // RP layers mask anything < 2 (less rare than 1-in-2-year) to keep the map
  // focused on actually-anomalous pixels.
  // Optionally also mask anomaly to the skill mask (r >= threshold), so anomalies
  // are only shown where the model has demonstrated skill.
  if (lastAnomResult) {
    var pct = lastAnomResult.select('pct');
    var rpW = lastAnomResult.select('rp_wet');
    var rpD = lastAnomResult.select('rp_dry');

    var clipSuffix = '';
    var skillMask  = null;
    if (filterSkillCheck.getValue() && clipAnomCheck.getValue() && lastResult) {
      var tClip = rSlider.getValue();
      skillMask = lastResult.select('correlation').gte(tClip);
      clipSuffix = ' [clipped to r≥' + tClip.toFixed(2) + ']';
    }
    function applyMasks(img) {
      if (skillMask) img = img.updateMask(skillMask);
      if (pspMask)   img = img.updateMask(pspMask);
      return img;
    }

    // The palettes already have very pale low-end colors, so RP near 1 renders
    // pale-pink/pale-blue naturally — no extra masking or fade needed.
    addManaged(applyMasks(pct), PCT_VIZ,    'Percentile — '   + lastAnomLabel + clipSuffix + pspSuffix, false);
    addManaged(applyMasks(rpW), RP_WET_VIZ, 'Wet RP (yrs) — ' + lastAnomLabel + clipSuffix + pspSuffix, false);
    addManaged(applyMasks(rpD), RP_DRY_VIZ, 'Dry RP (yrs) — ' + lastAnomLabel + clipSuffix + pspSuffix, true);
  }

  // Keep FAO boundaries on top — same layer object, no re-fetch
  Map.layers().remove(faoLayer);
  Map.layers().add(faoLayer);
}

rSlider.onChange(renderLayers);

Map.onClick(function(coords) {
  if (!lastResult && !lastAnomResult) return;
  var pt   = ee.Geometry.Point([coords.lon, coords.lat]);
  var bag  = {};
  if (lastResult)     bag.r   = lastResult.select('correlation').reduceRegion({reducer: ee.Reducer.first(), geometry: pt, scale: 50000}).get('correlation');
  if (lastAnomResult) {
    bag.pct = lastAnomResult.select('pct').reduceRegion({reducer: ee.Reducer.first(), geometry: pt, scale: 50000}).get('pct');
    bag.rpW = lastAnomResult.select('rp_wet').reduceRegion({reducer: ee.Reducer.first(), geometry: pt, scale: 50000}).get('rp_wet');
    bag.rpD = lastAnomResult.select('rp_dry').reduceRegion({reducer: ee.Reducer.first(), geometry: pt, scale: 50000}).get('rp_dry');
  }
  ee.Dictionary(bag).evaluate(function(v) {
    var parts = [];
    if (v.r   !== undefined) parts.push('r=' + (v.r !== null ? Number(v.r).toFixed(3) : '—'));
    if (v.pct !== undefined) parts.push('pct=' + (v.pct !== null ? Number(v.pct).toFixed(0) : '—'));
    if (v.rpW !== undefined) parts.push('wetRP=' + (v.rpW !== null ? Number(v.rpW).toFixed(1) : '—'));
    if (v.rpD !== undefined) parts.push('dryRP=' + (v.rpD !== null ? Number(v.rpD).toFixed(1) : '—'));
    inspectLabel.setValue(parts.join('  ') +
      '\n(' + coords.lon.toFixed(1) + '°, ' + coords.lat.toFixed(1) + '°)');
  });
});

// ── compute handler ───────────────────────────────────────────

computeBtn.onClick(function() {
  var issuedMonth = MONTH_NAMES.indexOf(issuedSelect.getValue()) + 1;
  var seasonKey   = seasonSelect.getValue();
  var startYear   = parseInt(startYearSelect.getValue(), 10);
  var endYear     = parseInt(endYearSelect.getValue(), 10);

  if (startYear >= endYear) { statusLabel.setValue('⚠ Start year must be before end year.'); return; }

  var lts = getLeadtimes(issuedMonth, SEASONS[seasonKey].months);
  if (lts.some(function(lt) { return lt > 6; })) {
    statusLabel.setValue('⚠ Some valid months need lt > 6 — no SEAS5 data.');
    return;
  }

  statusLabel.setValue('Computing…');
  inspectLabel.setValue('Click map to inspect pixel values.');
  lastResult = null;

  var result = computeCorrelation(issuedMonth, seasonKey, startYear, endYear);
  var n      = endYear - startYear + 1;
  lastResult = result;
  lastLabel  = seasonKey + ' | issued ' + issuedSelect.getValue() + ' | ' + startYear + '–' + endYear + ' (n=' + n + ')';
  renderLayers();
  statusLabel.setValue('✓ ' + lastLabel);
});

// ── Anomaly compute handler ───────────────────────────────────

computeAnomBtn.onClick(function() {
  var issuedMonth  = MONTH_NAMES.indexOf(issuedSelect.getValue()) + 1;
  var seasonKey    = seasonSelect.getValue();
  var startYear    = parseInt(startYearSelect.getValue(), 10);
  var endYear      = parseInt(endYearSelect.getValue(), 10);
  var forecastYear = parseInt(forecastYearSelect.getValue(), 10);

  if (startYear >= endYear) {
    anomStatusLabel.setValue('⚠ Start year must be before end year.'); return;
  }
  var lts = getLeadtimes(issuedMonth, SEASONS[seasonKey].months);
  if (lts.some(function(lt) { return lt > 6; })) {
    anomStatusLabel.setValue('⚠ Some valid months need lt > 6 — no SEAS5 data.'); return;
  }

  // climatology size after leave-one-out
  var inRange = (forecastYear >= startYear && forecastYear <= endYear);
  var nClim   = (endYear - startYear + 1) - (inRange ? 1 : 0);
  if (nClim < 5) {
    anomStatusLabel.setValue('⚠ Climatology too small (n=' + nClim + ') — widen the year range.'); return;
  }

  anomStatusLabel.setValue('Computing anomaly…');
  lastAnomResult = null;

  var result = computeAnomaly(issuedMonth, seasonKey, startYear, endYear, forecastYear);
  lastAnomResult = result;
  lastAnomLabel  = seasonKey + ' | issued ' + issuedSelect.getValue() + ' ' + forecastYear +
                   ' | clim ' + startYear + '–' + endYear + ' (N=' + nClim + ')';
  renderLayers();
  anomStatusLabel.setValue('✓ ' + lastAnomLabel);
});

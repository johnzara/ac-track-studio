/* Shared vocabularies: survey codes (compatible with the reference pipeline's
   streetview_segments.json), annotation kinds, and per-country data-source hints. */
(function () {
  "use strict";

  // `pipeline: true` = the reference pipeline (climb_furniture.py / ac_track.py) builds it today;
  // the others are understood by the brief and need new code in the build.
  const ROADSIDE = [
    { id: "none", label: "Open verge (grass / gravel)", pipeline: true },
    { id: "guardrail_steel", label: "Steel W-beam guardrail", pipeline: true },
    { id: "guardrail_wood", label: "Timber-clad guardrail", pipeline: true },
    { id: "cable_barrier", label: "Wire-rope (cable) barrier", pipeline: false },
    { id: "concrete_barrier", label: "Concrete barrier (New Jersey / F-shape)", pipeline: false },
    { id: "concrete_parapet", label: "Bridge parapet", pipeline: true },
    { id: "drystone_wall", label: "Dry-stone wall", pipeline: true },
    { id: "stone_wall", label: "Mortared stone wall", pipeline: true },
    { id: "retaining_stone", label: "Stone retaining wall", pipeline: true },
    { id: "retaining_concrete", label: "Concrete retaining wall", pipeline: true },
    { id: "plastered_wall", label: "Plastered / brick wall", pipeline: false },
    { id: "rock_face", label: "Rock cutting", pipeline: true },
    { id: "rock_netting", label: "Rock face with netting", pipeline: true },
    { id: "concrete_kerb", label: "Kerb / gutter edge", pipeline: true },
    { id: "sidewalk", label: "Raised sidewalk with kerb", pipeline: false },
    { id: "fence", label: "Timber / wire fence", pipeline: true },
    { id: "noise_barrier", label: "Noise barrier", pipeline: false },
    { id: "hedge", label: "Hedge", pipeline: true },
    { id: "building", label: "Building at the road edge", pipeline: true },
    { id: "ditch", label: "Ditch / open gully", pipeline: false },
    { id: "delineators", label: "Delineator posts only", pipeline: false },
    { id: "racing_kerb", label: "Racing kerb (red / white)", pipeline: false },
    { id: "armco", label: "Armco (double / triple rail)", pipeline: false },
    { id: "tyre_wall", label: "Tyre wall", pipeline: false },
    { id: "catch_fence", label: "Catch fence", pipeline: false },
    { id: "gravel_trap", label: "Gravel trap", pipeline: false },
    { id: "tarmac_runoff", label: "Tarmac run-off", pipeline: false },
  ];
  const SURFACE = ["new", "worn", "patched", "concrete", "cobbles", "gravel"];
  const EDGE_LINES = ["none", "left", "right", "both"];
  const CENTRE_LINE = ["none", "dashed", "solid", "double", "dashed_solid"];

  const ANNOTATION = [
    { id: "landmark", label: "Landmark (model by hand)", color: "#b45309" },
    { id: "building", label: "Building detail", color: "#7c3aed" },
    { id: "wall", label: "Wall", color: "#57534e" },
    { id: "guardrail", label: "Guardrail / barrier", color: "#0369a1" },
    { id: "kerb", label: "Kerb", color: "#dc2626" },
    { id: "fence", label: "Fence", color: "#a16207" },
    { id: "trees", label: "Trees / vegetation", color: "#15803d" },
    { id: "bridge", label: "Bridge / tunnel", color: "#475569" },
    { id: "sign", label: "Sign / marking", color: "#ca8a04" },
    { id: "pit", label: "Pit / paddock area", color: "#0f766e" },
    { id: "spectators", label: "Spectator area", color: "#be185d" },
    { id: "note", label: "Note", color: "#334155" },
  ];

  // Best-known open sources per country (ISO 3166-1 alpha-2). Verify licence and service URLs
  // before use; docs/DATA_SOURCES.md has the details and access methods.
  const SOURCES = {
    _global: {
      dem: "No national LiDAR: GEDTM30 (OpenGeoHub, 30 m bare-earth DTM, CC BY 4.0) or FABDEM (30 m bare earth, non-commercial: fine for a private track); Copernicus GLO-30 is a surface model (trees, buildings): horizon only",
      ortho: "No open global imagery fit for road edges; Sentinel-2 / EOxCloudless 2016–17 (10 m) for broad ground colour. Esri World Imagery only as a live reference to look at, never sampled or downloaded",
      topo: "OpenStreetMap; Overture Maps buildings (heights, roof shapes; ODbL)",
    },
    it: {
      dem: "No national LiDAR: by region (Alto Adige 0.5 / 2.5 m CC0 WCS; Friuli VG and Trentino 1 m; Piemonte 5 m CC BY WCS; Veneto 5 m IODL WMS; Emilia-Romagna 5 m; MASE PST 1 m on coasts and rivers); TINITALY 10 m (CC BY) everywhere",
      ortho: "AGEA orthophotos are all rights reserved (visual reference only); open: Alto Adige 2023 20 cm, Toscana 20 cm (CC BY); Friuli VG 10 cm (licence to check)",
      topo: "Piemonte BDTRE (carriageway polygons, building heights, CC BY); Alto Adige INSPIRE road areas (CC0); IGM DBSN (ODbL: road polygons on main roads, width classes, building heights); Veneto road elements (width class) and GeoDBT",
    },
    se: {
      dem: "Lantmäteriet Markhöjdmodell 1 m (CC BY 4.0; STAC + free Geotorget account)",
      ortho: "Lantmäteriet Ortofoto 0.16 m is CC BY but needs an approved GDPR application (WMS is paid): ask the user; else trace from the survey",
      topo: "Lantmäteriet Topografi 10 (centrelines, no width; buildings without height); Trafikverket NVDB widths (licence to verify)",
    },
    no: { dem: "Kartverket DTM1 1 m (open WCS, no key)", ortho: "No open national orthophoto (Norge i bilder needs a token)", topo: "NVDB 'Vegbredde' (type 838) road widths via the open API; N50" },
    fi: { dem: "MML KM2 2 m (CC BY, free API key); 0.5 p laser points for finer", ortho: "MML Ortokuva 0.5 m (CC BY, key)", topo: "Maastotietokanta (road class = width band, surface; CC BY, key)" },
    dk: { dem: "DHM/Terræn 0.4 m (CC BY, free token)", ortho: "Forårsbilleder 12.5 cm (CC BY, token)", topo: "GeoDanmark Vejkant road-edge lines (carriageway polygons); heights from DHM/Overflade" },
    ch: { dem: "swisstopo swissALTI3D 0.5 m (free, STAC)", ortho: "SWISSIMAGE 10 cm (WMTS / COG)", topo: "swissTLM3D (width classes, surface); swissBUILDINGS3D" },
    at: { dem: "BEV ALS DTM 1 m (CC BY, EPSG:3035 BigTIFF); Tirol / OÖ 0.5 m", ortho: "basemap.at 29 cm or BEV DOP 20 cm (CC BY)", topo: "BEV DLM (centrelines, buildings with heights)" },
    de: { dem: "DGM1 1 m, open in all 16 Länder (per-Land services; EPSG:25832, 25833 in the east)", ortho: "DOP20 in every Land (DOP10 in NRW, Bremen)", topo: "Basis-DLM (width attribute), ALKIS road areas; LoD2 buildings everywhere" },
    fr: { dem: "IGN LiDAR HD MNT 0.5 m (Licence Ouverte 2.0)", ortho: "IGN BD ORTHO 20 cm (WMTS, data.geopf.fr)", topo: "IGN BD TOPO (largeur_de_chaussee, building hauteur)" },
    es: { dem: "IGN MDT02 2 m (MDT 0.5 m in parts; CC BY-compatible)", ortho: "PNOA ~25 cm WMTS", topo: "IGR / BTN; Catastro buildings (floors)" },
    pt: { dem: "DGT MDT 0.5 m (free login; licence to check)", ortho: "DGT orthophotos 2025 25 cm (login)", topo: "No open road widths; OSM" },
    nl: { dem: "AHN 0.5 m (CC0)", ortho: "PDOK Luchtfoto 8–25 cm (open)", topo: "BGT road surface polygons; BAG / 3DBAG buildings" },
    be: { dem: "Flanders DHM-V II 1 m; Wallonia LiDAR (open)", ortho: "Flanders / Wallonia orthophotos (open)", topo: "Flanders GRB (road polygons); Wallonia PICC" },
    gb: { dem: "England EA LiDAR 1 m; Scotland 0.5–1 m (public S3); Wales 1 m COG (OGL)", ortho: "No open national orthophoto", topo: "OS Open Roads (centrelines, no width)" },
    ie: { dem: "GSI LiDAR 0.125–2 m (patchy)", ortho: "No open national orthophoto", topo: "Tailte Éireann 'Ways' road polygons (CC BY)" },
    cz: { dem: "ČÚZK DMR 5G (ground points; 2 m grid via REST; CC BY 4.0)", ortho: "ČÚZK orthophoto 12.5 cm", topo: "ZABAGED centrelines (no width)" },
    si: { dem: "ARSO 1 m (licence to check); CLSS 2023–25 point clouds", ortho: "GURS DOF025 25 cm", topo: "GURS road surface polygons ('Cestna površina'); cadastre building heights" },
    pl: { dem: "GUGiK NMT 1 m (0.5 m in places; free)", ortho: "GUGiK orthophoto 5–25 cm", topo: "BDOT10k (road surface width), LoD1/LoD2 buildings" },
    us: { dem: "USGS 3DEP 1 m (public domain)", ortho: "USDA NAIP 0.6 m (public domain)", topo: "City planimetrics; Microsoft building heights; lidar nDSM" },
    ca: { dem: "NRCan HRDEM 1–2 m (OGL-Canada)", ortho: "No national open orthophoto (Québec 20 cm CC BY)", topo: "NRN centrelines; NRCan auto-extracted buildings (heights)" },
    jp: { dem: "GSI DEM1A 1 m (~46% coverage), DEM5A 5 m", ortho: "GSI seamless photo ~0.5 m (credit 地理院タイル)", topo: "GSI road-edge lines; PLATEAU road polygons and LOD1/2 buildings" },
    nz: { dem: "LINZ 1 m DEM (CC BY; S3 / STAC)", ortho: "LINZ 7.5–30 cm (CC BY; S3 / STAC)", topo: "LINZ road centrelines (lane count); buildings without height (use DSM − DEM)" },
    au: { dem: "State LiDAR (e.g. QLD 0.5–1 m); Geoscience Australia 5 m", ortho: "State imagery (QLD ≥ 3 years old CC BY; others vary)", topo: "Road centrelines only; no open building heights" },
  };

  // Road-marking standard to follow, per country.
  const MARKINGS = {
    it: "Regolamento del Codice della strada, DPR 495/1992 art. 137–155, and DM 5/11/2001 (cross-sections)",
    se: "Transportstyrelsen TSFS 2010:171 (mittlinje, varningslinje, kantlinje) and Trafikverket VGU",
    no: "Statens vegvesen håndbok N302", fi: "Väylävirasto road-marking guide", dk: "Vejregler (afmærkning)",
    ch: "SN 640 850 (VSS)", at: "Bodenmarkierungsverordnung (BMVO)", de: "RMS (Richtlinien für die Markierung von Straßen)",
    fr: "IISR, 7e partie (marquage)", es: "Instrucción 8.2-IC", pt: "Regulamento de Sinalização do Trânsito",
    nl: "CROW richtlijnen markering", be: "Code de la route / wegcode", gb: "Traffic Signs Manual, chapter 5",
    us: "MUTCD", ca: "MUTCDC", jp: "道路標識・区画線及び道路標示に関する命令", nz: "MOTSAM / TCD Manual", au: "AS 1742.2",
  };

  // Typical roadside trees (for 3D models) by country; the build refines this from land cover.
  const TREES = {
    it: "downy / sessile oak, black locust (robinia), sweet chestnut, hop-hornbeam, Italian cypress, olive, stone pine, poplar",
    se: "Norway spruce, Scots pine, silver / downy birch, aspen, rowan, alder; juniper shrubs",
    no: "Norway spruce, Scots pine, birch, rowan, alder", fi: "Scots pine, Norway spruce, silver birch, aspen, alder",
    dk: "beech, oak, birch, Sitka / Norway spruce", ch: "Norway spruce, silver fir, beech, larch, sycamore maple",
    at: "Norway spruce, larch, beech, silver fir, Scots pine", de: "beech, oak, Norway spruce, Scots pine, birch",
    fr: "oak, beech, plane trees (avenues), maritime pine, chestnut", es: "holm oak, cork oak, Aleppo / stone pine, eucalyptus, olive",
    pt: "eucalyptus, maritime pine, cork oak, stone pine", nl: "oak, birch, poplar, willow, Scots pine",
    be: "beech, oak, birch, poplar", gb: "oak, ash, beech, hawthorn hedgerows, sycamore, Scots pine (Scotland)",
    us: "depends strongly on the state: map from land cover and Street View", jp: "Japanese cedar, cypress (hinoki), maple, bamboo",
    nz: "native podocarp/beech forest, radiata pine plantations", au: "eucalyptus species, acacia",
  };

  window.Vocab = { ROADSIDE, SURFACE, EDGE_LINES, CENTRE_LINE, ANNOTATION, SOURCES, MARKINGS, TREES,
    roadside: (id) => ROADSIDE.find((r) => r.id === id) || { id, label: id, pipeline: false },
    annotation: (id) => ANNOTATION.find((a) => a.id === id) || ANNOTATION[ANNOTATION.length - 1] };
})();

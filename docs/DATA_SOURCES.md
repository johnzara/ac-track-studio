# Open geodata by country

Where to get bare-earth elevation (DTM), orthophotos and topographic vectors (road edges / widths,
buildings with heights) for a studio track, researched for this studio on 2026-10-13. The planner
puts a one-line summary for the route's country into the brief (`planner/js/vocab.js` →
`SOURCES`); this file has the detail. Geoportals move often: re-check every URL and licence before
building, and record what you used in the build README.

## How to use it in a build

1. Find the country (and region / Land) below. Prefer a **bare-earth DTM ≤ 2 m**; without one, use
   GEDTM30 (global bare-earth, CC BY 4.0), never a surface model (Copernicus GLO-30, AW3D30) for
   the road corridor.
2. **Licence decides the use of an orthophoto.** Open (CC BY, CC0, OGL, dl-de, LO 2.0): road edges,
   ground colour, crowns, roofs. *All rights reserved* (e.g. AGEA in most of Italy), paid or
   application-only (e.g. Sweden): look at it as a visual reference, do not derive data or colours
   from it, and ask the user. Esri World Imagery: live viewing / manual tracing only. Google
   imagery and elevation: never (Maps Platform terms).
3. Road edges and widths, best first: carriageway polygons (NL BGT, Flanders GRB, Ireland "Ways",
   Slovenia, Alto Adige, Piemonte BDTRE, Japan PLATEAU) → width attributes (FR BD TOPO, PL
   BDOT10k, CH / FI width classes, NO NVDB) → tracing an open orthophoto → the Street View survey.
4. Normalise vertical datums before blending sources (see the gotchas below), and convert every
   source into the build's UTM frame.
5. Write each source as its own fetcher with the reference pipeline's output formats
   (docs/PIPELINE.md), so the rest of the pipeline does not change.

Researched 2026-10-13. Nine parallel research passes (8 regional/global plus API checks). Most endpoints were **hit live with curl** (GetCapabilities, STAC, ATOM, REST, HEAD/range requests). Licences come from official metadata or licence pages. Rules for reading this file:
- Anything marked **unverified** was not confirmed against an official source.
- The shared web-search budget ran out mid-research. Later checks were made by fetching official URLs directly, so some licence details remain unverified.
- Re-check URLs before relying on them, because geoportals move often.

**Legend:**
- Type: **DEM** = bare-earth terrain model, **ORTHO** = orthophoto, **TOPO** = vector roads and buildings.
- "Key" means a free API key, token or account is needed.
- Licences:
  - **Open and attribution-only** (fine for a published mod with credit): CC BY 4.0, OGL, dl-de/by, LO 2.0 (Licence Ouverte).
  - **No attribution needed**: CC0, dl-de/zero.
  - **ShareAlike**: ODbL (copyleft for derived databases).
  - **Not for published or commercial use**: NC (non-commercial).

## 0. Quick picks

| Country | Best open DTM | Best open ortho | Best road geometry / width | Buildings with height |
|---|---|---|---|---|
| Sweden | Markhöjdmodell 1 m (CC BY, account) | Ortofoto 0.16 m (CC BY, **GDPR application**) | Topografi 10 centrelines (no width); NVDB unverified | none open |
| Norway | DTM1 WCS (open, no key) | **none open** (Norge i bilder needs a token) | NVDB "Vegbredde" (838) API | Matrikkel points only |
| Finland | KM2 2 m (CC BY, key) | Ortokuva 0.5 m (CC BY, key) | Maastotietokanta class = width band | floor class only; 3D LoD2 partial |
| Denmark | DHM/Terræn **0.4 m** (CC BY, token) | Forårsbilleder 12.5 cm (CC BY, token) | GeoDanmark **Vejkant edge lines** | from DSM (DHM/Overflade) |
| Italy | patchwork: Alto Adige 0.5/2.5 m CC0 WCS; FVG/Trentino 1 m; MASE PST 1 m (coasts and rivers); elsewhere 5 m | Alto Adige 20 cm CC BY; Toscana 20 cm CC BY (WMS); AGEA is **all rights reserved** | Piemonte BDTRE (`ar_str`/`ac_vei` polygons); Alto Adige INSPIRE RoadArea (CC0); DBSN (ODbL) | Piemonte `un_vol_av`; DBSN `EDIFC_AT` |
| Switzerland | swissALTI3D 0.5 m (STAC) | SWISSIMAGE 10 cm WMTS | swissTLM3D width classes | swissBUILDINGS3D 3.0 β |
| Austria | BEV ALS DTM 1 m (CC BY, 50 km BigTIFF) | basemap.at 29 cm / BEV DOP 20 cm | Wien FMZK only | BEV DLM Bauwerke (heights) |
| Germany | **DGM1 open in all 16 Länder** | DOP20 everywhere (DOP10 in NRW and Bremen) | Basis-DLM axes (width attr.), ALKIS Straßenverkehr | **LoD2 everywhere** |
| France | LiDAR HD MNT 0.5 m (LO 2.0) | BD ORTHO 20 cm WMTS | BD TOPO `largeur_de_chaussee` | BD TOPO `hauteur` |
| Spain | MDT02 2 m national; MDT50cm partial | PNOA-MA ~25 cm WMTS | IGR lanes only | Catastro floors |
| Portugal | MDT-50cm (CC BY?, free login) | Ortos 2025 25 cm (login) | none open | none open |
| UK | EA 1 m (England), Scotland 0.5–1 m S3, Wales 1 m COG | **none open** | OS Open Roads centrelines | none open |
| Ireland | GSI LiDAR 0.125–2 m (patchy) | **none open** | **Tailte Éireann "Ways" road polygons** (CC BY) | none |
| Czechia | DMR 5G (LAZ ground points; 2 m via REST) | Ortofoto 12.5 cm | ZABAGED centrelines (no width) | RÚIAN floors |
| Slovenia | ARSO 1 m (licence unverified); CLSS 2023–25 LAZ | DOF025 25 cm | DTM "Cestna površina" polygons | KN heights (absolute) |
| Poland | NMT 1 m (0.5 m in places) WCS | Ortofotomapa 5–25 cm | BDOT10k `szerokoscNawierzchni` | LoD1/LoD2 nationwide, BDOT floors |
| USA | 3DEP 1 m (PD) | NAIP 0.6 m / NAIP Plus to 15 cm | city planimetrics only | MS GlobalML heights; lidar nDSM |
| Canada | HRDEM 1–2 m (OGL) | none national; Québec 20 cm CC BY | NRN centrelines | NRCan Auto-Extracted Buildings (heights) |
| Japan | GSI DEM1A 1 m tiles (~46% coverage), DEM5A | GSI seamlessphoto ~0.5 m | GSI 道路縁 edge lines; **PLATEAU road polygons** | PLATEAU LOD1/2 |
| New Zealand | LINZ 1 m DEM (S3/STAC, CC BY) | LINZ 7.5–30 cm (S3/STAC, CC BY) | Road Centrelines `lane_count` | outlines without height; use DSM−DEM |
| Australia | QLD QldDem 0.5–1 m ImageServer; ELVIS (no API); GA 5 m | QLD (≥3 yr old) CC BY; NSW unclear | lines only | none open |

**Cross-cutting gotchas**
- **Global DEMs are DSMs.** Copernicus GLO-30/90 and AW3D30 include canopy and buildings. GEDTM30 (CC BY 4.0) is the only open global bare-earth DTM found. FABDEM and FathomDEM are NC.
- **Vertical datums differ by country.** Heights are relative to NAP, DHHN2016, DHHN92 (Schleswig-Holstein), IGN69, ODN, Malin Head, EVRF2000-AT, LN02, Bpv, KRON86/EVRF2007, NAVD88, CGVD2013, NZVD2016, AHD, TP, EGM2008 or EGM96. Normalise them before blending sources.
- **Tile order varies.** basemap.at uses z/y/x; swisstopo and PDOK use z/x/y. ArcGIS REST uses `{z}/{y}/{x}`.
- **Google content is unusable for geometry.** Elevation values and satellite tracing are banned by the GMP Terms (see §5.5). Esri World Imagery may be used only for live manual tracing, not for colour sampling or caching (see §4.2).

---

## 1. Europe: national and regional sources
### 1.1 Sweden (Lantmäteriet; geotorget.lantmateriet.se)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | Markhöjdmodell Nedladdning (5810), STAC `dtm-cog` | 1 m, 10×10 km COG (~290 MB) | **CC BY 4.0** (not CC0) | STAC (open metadata): `https://api.lantmateriet.se/stac-hojd/v1/collections/dtm-cog/items?bbox=…`. Files `https://dl1.lantmateriet.se/hojd/data/grid/mhm/65_6/m657_67.tif` need **HTTP Basic auth** (free Geotorget account plus a free "order"). | EPSG:5845 (SWEREF99 TM + RH2000) | Data 2009–2026. No 2 m product any more. Rate limit returns 429. Breakline GPKG per tile. |
| DEM | Markhöjd Direkt (5808) | point/line queries | free | REST `https://api.lantmateriet.se/distribution/produkter/markhojd/v1` (OAuth2) | – | Spot heights only |
| DEM | Laserdata skog (`dsm-skoglig-copc`) | COPC point cloud | CC BY 4.0 + GDPR terms | same STAC | 5845 | Ground classification unverified. Older Laserdata NH is now **paid**. |
| ORTHO | Ortofoto Nedladdning (520112) | **0.16 m** RGBI (2019+), 2.5 km COG | CC BY 4.0, free, **but needs an approved GDPR application** | STAC `https://api.lantmateriet.se/stac-bild/v1/collections` (e.g. `orto-p2-2025`). Assets on `dl1.lantmateriet.se/bild/…` need Basic auth. | EPSG:3006 | The WMS (`maps.lantmateriet.se/ortofoto/wms/v1.3`) is **paid** |
| TOPO | Topografi 10 Nedladdning (vector) | 1:1k–20k | CC BY 4.0 (legal review needed) | Geotorget order, then GeoPackage per theme (e.g. `kommunikation_sverige.gpkg`) | EPSG:3006 | **Road centrelines only, no width.** Buildings have no height. |
| TOPO | Byggnad Nedladdning | – | CC BY 4.0 | STAC `https://api.lantmateriet.se/stac-vektor/v1/collections/byggnader/items` (Basic auth) | 3006 | No heights |
| TOPO | Trafikverket NVDB (road width) | – | **unverified** (believed open) | Lastkajen (unverified) | – | Probably the only open road-width source for Sweden |

### 1.2 Norway (Kartverket / Geonorge / hoydedata.no)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **DTM1 / NHM DTM** | 1 m (project DTMs down to 0.25 m) | CC BY 4.0 (data.norge.no) / NLOD 2.0 (ATOM). Both are open. | **WCS, no key:** `https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833?service=WCS&request=GetCapabilities` (also `-25832`, `-25835`), coverage `nhm_dtm_topo_25833`, max 3840×2160 px. ATOM `https://nedlasting.geonorge.no/geonorge/ATOM/hoydedata/datasett/DTM1.atom` (tiles e.g. `https://nedlasting.geonorge.no/hoydedata/DTM1/33-107-122.tif`, ~1.2 GB). ImageServer `https://hoydedata.no/arcgis/rest/services/NHM_DTM_25833/ImageServer/exportImage` (max 4096²). | EPSG:25832/25833/25835 + NN2000 | GetCoverage and exportImage tested OK. The seamless `hoyde-dtm_somlos` WCS is restricted. |
| ORTHO | Norge i bilder | varies | **Not open** (token) | `https://services.norgeibilder.no/wms/ortofoto`, `https://tilecache.norgeibilder.no/wmts/…` (499 "Token Required") | 25832/3/5, 3857 | **No open national orthophoto.** Opening is still under assessment in 2026. |
| TOPO | **NVDB "Vegbredde, beregnet" (type 838)** + NVDB road network | per segment | open (NLOD assumed, unverified) | `https://nvdbapiles.atlas.vegvesen.no/vegobjekter/api/v4/vegobjekter/838?kommune=…&inkluder=egenskaper` (header `X-Client` required, no key) | API CRS unverified | **Best open road-width source in Norway** (pavement width, min/max) |
| TOPO | N50 Kartdata | 1:50k | open (exact licence unverified) | Geonorge download API `https://nedlasting.geonorge.no/api/capabilities/` | 25833 | Coarse |
| TOPO | FKB-Veg / FKB-Bygning | 1:1k | **restricted** (Norge digitalt) | member login | – | Has carriageway edges, but is not open |
| TOPO | Matrikkelen Bygningspunkt | points | open | WFS `https://wfs.geonorge.no/skwms1/wfs.matrikkelen-bygningspunkt` | – | Points only, no footprints or heights |

### 1.3 Finland (Maanmittauslaitos, MML). **Every service needs a free API key** (`?api-key=`)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | Korkeusmalli 2 m (KM2) | **2 m** (no open 1 m product) | CC BY 4.0 | WCS 2.0.1 `https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2`, CoverageId `korkeusmalli_2m` (max 10×10 km / 5000 px). OGC API Processes `https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1/processes/korkeusmalli_2m_bbox` | EPSG:3067 + N2000 | Returns 401 without a key. ±0.3 m. |
| DEM | Laser 0.5 p (open) / 5 p (paid) point clouds | points | 0.5 p: CC BY 4.0. 5 p: €0.033/km², **no republishing** | process `laserkeilausaineisto_05_karttalehti` | 3067 | The only route to a sub-2 m Finnish DTM is to grid these yourself |
| ORTHO | Ortokuva (colour/CIR) | 0.5 m | CC BY 4.0 | WCS same endpoint, `ortokuva_vari` (max 2×2 km). WMTS `https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0/WMTSCapabilities.xml` | 3067 (WMTS 3857) | 3-year update cycle |
| TOPO | **Maastotietokanta** | 1:5k–10k | CC BY 4.0 | OGC API Features `https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/collections/{tieviiva,rakennus}/items?api-key=` | 3067; 3D (EPSG:10774) since 2025 | Road centrelines whose **class encodes width**: Ia/Ib >8 m, IIa 6.5–8, IIb 5–6.5, IIIa 4–5, IIIb 3–4, Ajotie <3. Has surface and level attributes. Buildings have a floor-count class but no height. |
| TOPO | Rakennukset 3D | LoD2 CityGML | CC BY 4.0 | process `3d-rakennukset_karttalehti` | 3067 + N2000 | Partial coverage |

### 1.4 Denmark (Klimadatastyrelsen, ex-SDFI; Dataforsyningen). **Free token needed** (`&token=`)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **DHM/Terræn** | **0.4 m** (σz 0.06 m) | CC BY 4.0 | WCS 1.0.0 `https://api.dataforsyningen.dk/dhm_wcs_DAF?service=WCS&request=GetCoverage&coverage=dhm_terraen&token=…` (also `dhm_overflade` DSM). Files `ftps://ftp.dataforsyningen.dk/dhm_danmarks_hoejdemodel/DTM`. Quick-DHM `https://api.dataforsyningen.dk/wcs/dhm_temp`. | EPSG:25832, DVR90 (unverified) | Without a token GetCoverage returns 403; GetCapabilities works. Tile scheme unverified. Datafordeler service phase-out in 2026 is unverified. |
| ORTHO | Forårsbilleder (spring orthophoto) GeoDanmark | **12.5 cm** nationwide, 10 cm in some municipalities | CC BY 4.0 | WMS `https://api.dataforsyningen.dk/orto_foraar_DAF?…&token=` (layers `orto_foraar`, `geodanmark_2025_12_5cm`, …). WMTS `orto_foraar_wmts_DAF`, `orto_foraar_webm_DAF` (3857) | 25832 (+3857) | Yearly, published each November |
| TOPO | **GeoDanmark Vektor** (spec 6.0.2) | 1:1k | CC BY 4.0 | Datafordeler WFS/file download (auth details unverified). WMS `GeoDanmark_60_NOHIST_DAF` (token). | 25832 | **`Vejkant` road-edge lines**, so carriageway polygons can be built. No width attribute. `Bygning` polygons have no height (use DSM). |
| TOPO | INSPIRE Buildings | points | CC BY 4.0 | `https://api.dataforsyningen.dk/ogcapi/features/building_inspire/collections/building/items` (no token) | – | Floor and height fields often null |

### 1.5 Italy

National notes:
- There is **no national wall-to-wall LiDAR**. A PNRR M2C4 national programme is in progress, with no product yet (unverified).
- **AGEA orthophotos are "tutti i diritti riservati"** (all rights reserved) everywhere. The only open regional exception found is Alto Adige 2023.
- **IGM DBSN is ODbL**, not CC BY.

| Region | Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|---|
| National | DEM | MASE PST LiDAR DTM/DSM (`EL.LIDAR.<REGIONE>.1x1.DTM`) | 1 m / 2 m | CC BY 4.0 | WMS `http://wms.pcn.minambiente.it/ogc?map=/ms_ogc/WMS_v1.3/servizi-LiDAR/LIDAR_<REGIONE>.map`. Download app "Download Banca Dati PST" at `https://sim.mase.gov.it/portalediaccesso/mappe/#/viewer/new` (GeoTIFF, ≤6 GB) | WMS 32632/32633/3003/3004 | **Coasts, rivers and risk areas only** (2008–15). **No WCS.** Whether the app needs login is unverified. |
| National | DEM | TINITALY/1.1 (INGV) | 10 m | CC BY 4.0 (doi:10.13127/tinitaly/1.1) | Tile ZIPs at `https://tinitaly.pi.ingv.it/Download_Area1_1.html` | EPSG:32632 | Whole of Italy. Fallback. |
| National | DEM | PCN DTM 20 m | 20 m | licence unverified | WCS `http://wms.pcn.minambiente.it/ogc?map=/ms_ogc/wcs/DTM_20M.map` (`EL.DTM.20M`) | 32632/33 | Fallback |
| National | ORTHO | PCN orthophoto 2012 | ~0.5 m (unverified) | view only, licence unverified | WMS `…/WMS_v1.3/raster/ortofoto_colore_12.map` | 32632/33 | Outdated |
| National | TOPO | **IGM DBSN** (2025-07-28) | medium scale | **ODbL 1.0** | Per-province FileGDB after registration at `https://www.igmi.org/it/dbsn-database-di-sintesi-nazionale` | RDN2008 (EPSG unverified) | `AR_STR` road polygons (main roads). `TR_STR_CL` width class (<3.5 / 3.5–7 / >7 m). `EDIFC_AT` building height. |
| Veneto | DEM | Regional DTM (40+ LiDAR campaigns, >80% coverage) | 5 m (partial 2 m `rv:DTM_2m_clip`) | IODL 2.0 | WMS `https://idt2-geoserver.regione.veneto.it/geoserver/wms`, layer `rv:DTM_RV_5m_3003`. Tile index WFS `rv:c0101071_lidar5m`. **WCS disabled.** | 3003 / EPSG:6876 | Native 1 m only through MASE PST. Raster download path unverified. |
| Veneto | ORTHO | AGEA 2024/2021/2018 | 20 cm | **AGEA, all rights reserved** | same WMS, `rv:ortofoto_agea_2024` | 3003 | Visual reference only |
| Veneto | TOPO | Elemento stradale; Edifici del Veneto 2022 | 1:5k–10k | IODL 2.0 | WFS `https://idt2-geoserver.regione.veneto.it/geoserver/wfs`, `rv:c01070240012_elementostradale` (width class `c_largh`) and `rv:edifici_veneto_feb2022` | 3003 / 6876 | No road polygons, no building heights |
| Lombardia | DEM | DTM 5x5 ed. 2015 (mostly from DBT; LiDAR only along rivers) | 5 m | unverified | ZIP `https://www.cartografia.servizirl.it/download/DTM5_RL.zip` (3 GB). ImageServer `https://www.cartografia.servizirl.it/arcgis2/rest/services/BaseMap/DTM5_RL_img/ImageServer` (exportImage OK) | EPSG:32632 | No recent open regional LiDAR |
| Lombardia | ORTHO | AGEA 2021 | 20 cm | AGEA, view only | ImageServer `…/arcgis2/rest/services/BaseMap/Ortofoto2021/ImageServer` | 3857 | Reference only |
| Lombardia | TOPO | DBGT | 1:1k–10k | unverified | geoportale.regione.lombardia.it download | 25832 | Contents (AC_VEI/UN_VOL) unverified |
| Piemonte | DEM | RIPRESA AEREA ICE 2009-11 DTM (LiDAR) | 5 m | CC BY 4.0 | WCS `https://geomap.reteunitaria.piemonte.it/ws/taims/rp-01/taimsdtmwcs/wcs_ice_2009_2011_dtm` (CoverageId `DTM`) | EPSG:32632 | Whole region. No newer LiDAR found. |
| Piemonte | ORTHO | ICE 2009-11; AGEA 2024 | AGEA 30 cm | ICE unverified; AGEA all rights reserved | WMS/WMTS `https://geomap.reteunitaria.piemonte.it/mapproxy/service` (`rp_ortofoto_2010`) | 25832 | – |
| Piemonte | TOPO | **BDTRE 2026** | 1:5k–10k | **CC BY 4.0** | WFS `https://geoservices.csi.it/ms/wfs/taims/rp-01/taimswfs/bdtre_viab` (`ac_vei` and `ar_str` polygons; `el_str_cl` width class) and `…/bdtre_imm` (`un_vol` with height `un_vol_av`) | EPSG:32632 | **Best Italian fit:** road polygons plus building heights |
| Toscana | DEM | DTM 1 m IRS LiDAR 2019–21 | 1 m | **NOT open** (IRS-owned) | view-only WMS `https://www502.regione.toscana.it/wmsraster/com.rt.wms.RTmap/wms?map=wmsmorfologia` | 25832/3003 | Open alternatives: `rt_morfologia.iddtm.10m.rt` (10 m), DSM 2021 1 m (CC BY), MASE PST 1 m |
| Toscana | ORTHO | Regional ortho 2021/2023/2024-25 | 20 cm RGB+NIR | **CC BY 4.0** | WMS `https://www502.regione.toscana.it/ows_ofc/com.rt.wms.RTmap/wms?map=owsofc_rt`. Full-resolution TIFF by email to contatto.cartografia@regione.toscana.it | 25832/3003 | The AGEA layers (`map=owsofc`) are restricted |
| Toscana | TOPO | Edificato – unità volumetriche 2023; CTRN/DBT 1:2k/10k | 1:2k–10k | CC BY | Geoscopio `https://www502.regione.toscana.it/geoscopio/cartoteca.html` | EPSG:3003 | Buildings with ground and eave heights. Road polygons unverified. |
| Trentino | DEM | LiDAR PAT 2014 (+2018) DTM/DSM/LAZ | ~1 m (unverified), 500 m tiles | CC BY 4.0 | STEM `https://siat.provincia.tn.it/stem/`. Tile index WFS `https://siat.provincia.tn.it/geoserver/wfs` (`stem:inqlid2014_dtm_asc`) | EPSG:25832 | No WCS. Direct HTTP tile pattern unverified. |
| Trentino | ORTHO | PAT ortho 2015 (in STEM) | unverified | CC BY 4.0 (unverified) | STEM | 25832 | No open WMS found |
| Trentino | TOPO | Carta Tecnica Provinciale 2017 | 1:5k–10k | CC BY 4.0 | territorio.provincia.tn.it | 25832 | Road areas and heights unverified |
| Alto Adige | DEM | **DTM 2.5 m (whole province), 0.5 m (settlements), 0.2 m (Adige 2024)** | 2.5 / 0.5 / 0.2 m | **CC0 1.0** | **WCS 2.0.1** `https://geoservices9.civis.bz.it/geoserver/ows?service=WCS&version=2.0.1&request=GetCapabilities`, IDs `p_bz-Elevation__DigitalTerrainModel-2.5m`, `…-0.5m`, `…-EtschAdige-0.2m-2024` | EPSG:25832 | **Best Italian programmatic access.** The old `geoservices.buergernetz.bz.it/geoserver` returns 404. |
| Alto Adige | ORTHO | Ortho 2023 | 20 cm | **CC BY 4.0** | WMS/WMTS `https://geoservices.buergernetz.bz.it/mapproxy/ows`, layer `p_bz-Orthoimagery:Aerial-2023-RGB` | 25832 | The only open AGEA-derived ortho found |
| Alto Adige | TOPO | INSPIRE RoadArea, VehicleTrafficArea, Building | large scale | **CC0** | WFS `https://geoservices8.civis.bz.it/geoserver/p_bz-Inspire/ows` | 25832 | **Carriageway polygons.** Building height values unverified. |
| FVG | DEM | Regional LiDAR 2017–20 DTM | 1 m and 0.5 m | IODL 2.0 (default; per-dataset unverified) | IRDAT `https://irdat.regione.fvg.it/consultatore-dati-ambientali-territoriali/`, eagle.fvg. Tile index WFS `https://serviziogc.regione.fvg.it/geoserver/ows` (`GRIGLIEGEO:QU_DTM_LIDAR_FVG`) | EPSG:6708 | No WCS |
| FVG | ORTHO | True ortho 2017–20 | **10 cm** | unverified (IODL default) | index WFS `GRIGLIEGEO:QU_TRUEORTO1720_FVG`; download via eagle/IRDAT | 6708 | Ideal for texturing if the licence holds |
| FVG | TOPO | DBTfvg (partial), DBTsped (ML-vectorised buildings and roads), CTRN 1:5000 | 1:1k–5k | IODL 2.0 (unverified) | eagle.fvg / IRDAT | 6708 | Patchy coverage |
| Emilia-Romagna | DEM | DTM 5x5 ed. 2025 (LiDAR 2023/24); DTM 0.5 m along rivers; coastal 1 m | 5 / 0.5 / 1 m | CC BY 4.0 | WCS `https://servizigis.regione.emilia-romagna.it/wcs/dtm5x5`, `…/wcs/dtm_cgr_emg_fiumi_202305` | EPSG:7791 | Full-resolution 2023/24 release unverified |
| Liguria | DEM | DTM ed. 2023 (photogrammetric) | 5 m | CC BY 4.0 | WCS `https://geoservizi.regione.liguria.it/geoserver/M2056/wcs` | 3003 | 1 m only via MASE |
| Valle d'Aosta / Sardegna | DEM | MASE PST 1 m; Sardegna 1 m coastal and urban | 1 m | CC BY 4.0 (MASE); regional licence unverified | MASE WMS; `https://www.sardegnageoportale.it/webgis2/sardegnamappe/?map=download_raster` | – | Partial |

### 1.6 Switzerland (swisstopo; free geodata, attribution "© swisstopo", commercial use OK)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **swissALTI3D** | **0.5 m** and 2 m, 1 km COG | swisstopo free-geodata terms (STAC wrongly says "proprietary") | STAC `https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d/items`. Asset pattern `https://data.geo.admin.ch/ch.swisstopo.swissalti3d/swissalti3d_{YYYY}_{E}-{N}/swissalti3d_{YYYY}_{E}-{N}_0.5_2056_5728.tif` | EPSG:2056 + LN02 (5728) | ±0.3 m. DSM: `ch.swisstopo.swisssurface3d-raster` 0.5 m. |
| ORTHO | **SWISSIMAGE 10 cm** | 10 cm (25 cm in the Alps) | same | WMTS `https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg`. COG via STAC `ch.swisstopo.swissimage-dop10` | 2056 / 3857 | 3-year cycle |
| TOPO | swissTLM3D 2.4 | 3D vectors, 0.2–1.5 m | same | STAC `ch.swisstopo.swisstlm3d` (gpkg/gdb/shp) | 2056/5728 | Centrelines with **width classes** in `OBJEKTART`: 10m Strasse >10.2 m, 8m 8.21–10.2, 6m 6.21–8.2, 4m 4.21–6.2, 3m 2.81–4.2, etc. Also `BELAGSART` (surface). Building footprints have no height. |
| TOPO | swissBUILDINGS3D 3.0 (Beta) / 2.0 | LoD2+ roofs | same | STAC `ch.swisstopo.swissbuildings3d_3_0` | 2056/5728 | – |

### 1.7 Austria

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **BEV ALS DTM Höhenraster (national)** | 1 m, 50×50 km BigTIFF (~7.7 GB, range-readable) | CC BY 4.0 (metadata; product pages still cite UrhG) | ATOM `https://data.bev.gv.at/geonetwork/srv/atom/describe/service?uuid=208cff7a-c8aa-42fe-bf4f-2b8156e37528`. Tiles `https://data.bev.gv.at/download/ALS/DTM/{YYYYMMDD}/ALS_DTM_CRS3035RES50000mN{N}E{E}.tif` (dates 20190915 … 20250915). Index `https://data.bev.gv.at/download/ALS/ALS_Kacheluebersicht.zip` | **EPSG:3035** + EVRF2000-AT (2021 series: 5778) | Read windows with `/vsicurl/`. No WCS. A DSM also exists. |
| DEM | State DGMs: Tirol 0.5 m, OÖ 0.5 m, Steiermark/Salzburg/Wien 1 m | 0.5–1 m | CC BY 4.0 | Tirol WCS `https://gis.tirol.gv.at/arcgis/services/Service_Public/terrain/MapServer/WCSServer` (`Gelaendemodell_50cm_M28/M31`). Steiermark WCS `https://gis.stmk.gv.at/arcgis/services/OGD/ALSGelaendeinformation_1m_UTM33N/MapServer/WCSServer`. Wien `https://www.wien.gv.at/ma41datenviewer/downloads/geodaten/dgm_tif/{blatt}_dgm_tif.zip` | 31254/31255/32633/31256 | Vorarlberg, NÖ, Kärnten and Burgenland 1 m unverified (the BEV national DTM covers them) |
| DEM | geoland DGM 10 m | 10 m | CC BY 4.0 | `https://gis.ktn.gv.at/OGD/Geographie_Planung/ogd-10m-at.zip` | EPSG:31287 | Fallback |
| ORTHO | **basemap.at Orthofoto** | 29 cm (15 cm in places) | CC BY 4.0 ("Grundkarte: basemap.at") | WMTS `https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg` (**z/y/x**) | EPSG:3857 | Updated yearly |
| ORTHO | BEV DOP | 20 cm | CC BY 4.0 | ATOM uuid `7f047345-4ebf-45cd-8900-6edf50a84638`. `https://data.bev.gv.at/download/DOP/20220128/DOP_CRS3035RES50000mN{N}E{E}_20220128.tif` (~12 GB, range OK) | 3035 / 31254 | The WMS needs a token |
| TOPO | BEV DLM Verkehr | centrelines | CC BY 4.0 | `https://data.bev.gv.at/download/DLM/DLM_20260323/DLM_1000_VERKEHR_20260323.gpkg` | EPSG:31255 | No width or lanes |
| TOPO | **BEV DLM Bauwerke** | 2.5D / LoD1 | CC BY 4.0 | `https://data.bev.gv.at/download/DLM/DLM_20260323/DLM_8000_BAUWERK_20260323.gpkg` | 31255 + 5778 | Footprints with ground, mean and max height |
| TOPO | Wien FMZK | area-based | CC BY 4.0 | WFS `https://data.wien.gv.at/daten/geo?service=WFS…` (layer name unverified) | 31256 | Carriageway = `F_KLASSE` 21. Vienna only. |

### 1.8 Germany: per Land. **DGM1, DOP20 and LoD2 are open in all 16 Länder.** BKG's federal DGM1 is **not** free (~€8,000).

All 16 Länder (DE) report "dl-de/zero" or "dl-de/by" (Datenlizenz Deutschland), and all deliver GeoTIFF/XYZ tiles in EPSG:25832, except Berlin, Brandenburg, Mecklenburg-Vorpommern and Sachsen, which use **EPSG:25833**. Heights are DHHN2016, except Schleswig-Holstein (DHHN92). You need a per-Land adapter.

| Land | Type | Product / licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|
| Baden-Württemberg | DEM | DGM1 (also **DGM025 0.25 m**), dl-de/by-2.0 | `https://opengeodata.lgl-bw.de/data/dgm/dgm1_32_{E}_{N}_2_bw.zip` | 25832 | 2×2 km **XYZ** zip (E odd, N even km) |
| Baden-Württemberg | ORTHO / TOPO | DOP20 `…/data/dop20/dop20rgb_32_{E}_{N}_2_bw.zip`; LoD2 `…/data/lod2/LoD2_32_{E}_{N}_2_bw.zip`; ALKIS, LN, Basis-DLM under `/data/` | as left | 25832 | Public WMS unverified (the portal uses an authenticated proxy) |
| Bayern | DEM | DGM1, **CC BY 4.0** | Metalink `https://geodaten.bayern.de/odd/a/dgm/dgm1/meta/metalink/{AGS}.meta4` → `https://download1.bayernwolke.de/a/dgm/dgm1/{E}_{N}.tif` | 25832 | 1×1 km float32, nodata −9999 |
| Bayern | ORTHO / TOPO | DOP20 WMS `https://geoservices.bayern.de/od/wms/dop/v1/dop20`; tiles `download1.bayernwolke.de/a/dop20/data/32{E}_{N}.tif`; LoD2 `…/a/lod2/citygml/{E}_{N}.gml`; product list `https://geodaten.bayern.de/opengeodata/json/opengeodata_produkte.json` | as left | 25832 | – |
| Berlin | DEM | DGM1, dl-de/zero-2.0 | ATOM `https://gdi.berlin.de/data/dgm1/atom/` → `DGM1_{E}_{N}.zip` | **25833** | 2×2 km XYZ |
| Berlin | ORTHO / TOPO | DOP20/TrueDOP 2025 WMS `https://gdi.berlin.de/services/wms/dop_2025_fruehjahr`; LoD2 ATOM `https://gdi.berlin.de/data/a_lod2/atom/`; ALKIS WFS `https://gdi.berlin.de/services/wfs/alkis_gebaeude` | as left | 25833 | – |
| Brandenburg | DEM | DGM1, dl-de/by-2.0 | `https://data.geobasis-bb.de/geobasis/daten/dgm/tif/` (open directory listing) | **25833** | 1×1 km |
| Brandenburg | ORTHO / TOPO | DOP20 `…/daten/dop/rgbi_tif/`; WMS `https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms`; LoD2 `…/daten/3d_gebaeude/lod2_gml/`; ALKIS `…/daten/alkis/` | as left | 25833 | – |
| Bremen | DEM | DGM1 (2017 / 2015), CC BY 4.0 | `https://gdi2.geo.bremen.de/inspire/download/DGM/data/Gitternetz_DGM1_2017_HB_ASCII_XYZ.zip` | 25832 | Old data |
| Bremen | ORTHO / TOPO | **DOP10** WMS `https://geodienste.bremen.de/wms_dop10_2023`; LoD2, ALKIS and Basis-DLM via `https://www.geo.bremen.de/produkte/open-data-produktuebersicht-15654` | as left | 25832 | – |
| Hamburg | DEM | DGM1 2022, dl-de/by-2.0 | `https://daten-hamburg.de/opendata/fernerkundung_hoehenmodelle/dgm/dgm1_hh_2022-04-30.zip`; WMS `https://geodienste.hamburg.de/wms_gelaendemodelle_dgm` | 25832 | Whole-city file |
| Hamburg | ORTHO / TOPO | DOP WMS `https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt`; LoD2, **LoD3**, ALKIS "ausgewählte Daten"; HH-SIB (lanes, speed) via `https://suche.transparenz.hamburg.de` | as left | 25832 | Best German city extras |
| Hessen | DEM | DGM1, dl-de/zero (open since 2022) | Downloadcenter `https://gds.hessen.de/INTERSHOP/web/WFS/HLBG-Geodaten-Site/de_DE/-/EUR/ViewDownloadcenter-Start` | 25832 | Tile scheme and file naming unverified |
| Hessen | ORTHO / TOPO | DOP20 WMS `https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows`; LoD2, ALKIS and Basis-DLM via the Downloadcenter | as left | 25832 | – |
| Mecklenburg-Vorpommern | DEM | DGM1, CC BY 4.0 | ATOM `https://www.geodaten-mv.de/dienste/dgm_atom` (pick the "Gtiff" or XYZ entries, not "Zcoding"/"Shading") | **25833** | 2×2 km |
| Mecklenburg-Vorpommern | ORTHO / TOPO | DOP20 ATOM `https://www.geodaten-mv.de/dienste/dop20_atom`, WMS `https://www.geodaten-mv.de/dienste/adv_dop`; LoD2 ATOM `…/gebaeude_atom` | as left | 25833 | – |
| Niedersachsen | DEM | DGM1, CC BY 4.0 (AGNB §7 attribution rules) | Index `https://arcgis-geojson.s3.eu-de.cloud-object-storage.appdomain.cloud/dgm1/lgln-opengeodata-dgm1.geojson` → `https://dgm1.s3.eu-de.cloud-object-storage.appdomain.cloud/…/dgm1_32_{E}_{N}_1_ni_{yyyy}.tif` | 25832 | 1×1 km |
| Niedersachsen | ORTHO / TOPO | DOP20 WMS `https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms`; S3 `dop20-rgb.s3.eu-de…`; LoD2 and ALKIS FeatureServers on `https://ni-lgln-opengeodata.hub.arcgis.com` | as left | 25832 | – |
| Nordrhein-Westfalen | DEM | DGM1, dl-de/zero-2.0 | `https://www.opengeodata.nrw.de/produkte/geobasis/hm/dgm1_tiff/dgm1_tiff/` (`index.json`) → `dgm1_32_{E}_{N}_1_nw_{yyyy}.tif` | 25832 | **The `dgm1_xyz/` path returns 404 now** |
| Nordrhein-Westfalen | ORTHO / TOPO | **DOP10** `…/geobasis/lusat/akt/dop/dop_jp2_f10/`; WMS `https://www.wms.nrw.de/geobasis/wms_nw_dop`; LoD2 `…/geobasis/3dg/lod2_gml/`; Basis-DLM `…/lm/akt/basis-dlm/` | as left | 25832 | – |
| Rheinland-Pfalz | DEM | DGM1, dl-de/by-2.0 | `https://geobasis-rlp.de/data/dgm1/current/tif/` | 25832 | 1×1 km |
| Rheinland-Pfalz | ORTHO / TOPO | DOP20 `…/data/dop20rgb/current/jp2/`; WMS `https://geo4.service24.rlp.de/wms/rp_dop20.fcgi`; LoD2 `…/data/geb3dlo/current/gml/`; Basis-DLM `…/data/basisdlm/current/` | as left | 25832 | Open ALKIS vector unverified |
| Saarland | DEM | DGM1 2025 (new LiDAR), dl-de/by-2.0 | Nextcloud `https://www.shop.lvgl.saarland.de/cloud/index.php/s/NK8ndP55qAqGEZD?dir=/OD_DGM1_2025_tif_LK` | 25832 | Per-district zips (0.5–1.1 GB) |
| Saarland | ORTHO / TOPO | TrueDOP20 2025 in the same share (24–46 GB zips); LoD2, ALKIS and Basis-DLM in `OD_*` folders | as left | 25832 | WMS unverified |
| Sachsen | DEM | DGM1, dl-de/by-2.0 | `https://geoviewer.sachsen.de/mapviewer/resources/apps/produktdownload/index.html?lang=de&products=DGM1`; batch download per municipality | **25833** | 2×2 km |
| Sachsen | ORTHO / TOPO | DOP20 WMS `https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest`; LoD2, ALKIS and Basis-DLM via geodaten.sachsen.de | as left | 25833 | – |
| Sachsen-Anhalt | DEM | DGM1, dl-de/by-2.0 | WCS `https://www.geodatenportal.sachsen-anhalt.de/ows_WCS_ST_DGM1`; tile picker `https://www.lvermgeo.sachsen-anhalt.de/de/gdp-dgm1.html` | 25832 | 2×2 km |
| Sachsen-Anhalt | ORTHO / TOPO | DOP20 WMS `https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest`; LoD2 WFS `https://geodatenportal.sachsen-anhalt.de/ows_ST_LVermGeo_LoD2_WFS`; Basis-DLM shp zip | as left | 25832 | – |
| Schleswig-Holstein | DEM | DGM1, CC BY 4.0 | Index `https://geodaten.schleswig-holstein.de/gaialight-sh/_apps/dladownload/single.php?file=DGM1_SH__Massendownload.geojson&id=4` | 25832, **DHHN92** | 1×1 km, XYZ only. Incomplete TLS certificate chain. |
| Schleswig-Holstein | ORTHO / TOPO | DOP20 WMS `https://dienste.gdi-sh.de/WMS_SH_DOP20col_OpenGBD`; LoD2, ALKIS and Basis-DLM via `…/dladownload/` | as left | 25832 | – |
| Thüringen | DEM | DGM1, CC BY 4.0 / dl-de/by (sources conflict) | ATOM `https://geoportal.geoportal-th.de/dienste/atom_th_hoehendaten_dgm` | 25832 | geoportal.thueringen.de is behind a CAPTCHA, so use geoportal-th.de |
| Thüringen | ORTHO / TOPO | DOP20 WMS `https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP20`; 3D buildings and ALKIS via `…/gaialight-th/_apps/dladownload/` | as left | 25832 | – |
| Federal | DEM | basemap.de 3D Gelände (DGM5, CC BY 4.0); Sonny's DTM (CC BY 4.0, ~30 m) | Terrain-RGB `https://sgx.geodatenzentrum.de/gdz_basemapde_3d_gelaende/dgm5_3857_rgb.json`; `https://sonny.4lima.de` | 3857 / 4326 | Seamless but only 5 m |

German roads: **no Land publishes true carriageway (Fahrbahn) polygons.**
- ALKIS `AX_Strassenverkehr` covers the whole street parcel.
- ATKIS Basis-DLM `AX_Strassenachse` / `AX_Fahrbahnachse` carry width and lane attributes in the AAA model. How completely each Land fills them is unverified.
- Practical stack: DGM1 + DOP10/20 + Basis-DLM axes + OSM.

### 1.9 France (IGN Géoplateforme: `data.geopf.fr`; wxs.ign.fr is retired). Licence Ouverte 2.0 (LO 2.0)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **MNT LiDAR HD** | **0.5 m**, 1×1 km tiles (2000² Float32) | LO 2.0 | **Tile index:** vector tiles `https://data.geopf.fr/tms/1.0.0/IGNF_MNT-LIDAR-HD-produit/{z}/{x}/{y}.pbf` (z10–16, layer `mnt_dalle`, attribute `url`). **Each tile is a WMS-R GetMap:** `https://data.geopf.fr/wms-r?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93&FORMAT=image/geotiff&CRS=EPSG:2154&BBOX=…&WIDTH=2000&HEIGHT=2000`. WMS-R also serves `image/x-bil;bits=32` for any bbox. | EPSG:2154 + IGN69 | **No WCS.** Full metro coverage is targeted for 2026 (progress: https://macarte.ign.fr/carte/mThSup/diffusionMNxLiDARHD). ZICAD (restricted) zones are nodata. The old WFS tile index returns 403. |
| DEM | RGE ALTI | 1 m / 5 m | LO 2.0 | Per-département download `https://data.geopf.fr/telechargement/resource/RGEALTI`. WMS-R layer `ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES`. Point API `https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json?lon=..&lat=..&resource=ign_rge_alti_wld` | 2154 / IGN69 | Fallback where LiDAR HD is missing |
| ORTHO | **BD ORTHO / ORTHO HR** | 20 cm (15 cm in places) | LO 2.0 | WMTS `https://data.geopf.fr/wmts`, layers `HR.ORTHOIMAGERY.ORTHOPHOTOS` (PM_6_19) and `ORTHOIMAGERY.ORTHOPHOTOS`. WMS-R `https://data.geopf.fr/wms-r/wms` (`FORMAT=image/geotiff`). JP2 `https://data.geopf.fr/telechargement/resource/BDORTHO` | 3857 / 2154 | `THR.ORTHOIMAGERY.ORTHOPHOTOS` (5–10 cm) licence unverified |
| TOPO | **BD TOPO V3** | ~1 m | LO 2.0 | WFS `https://data.geopf.fr/wfs/ows`, typeNames `BDTOPO_V3:troncon_de_route`, `BDTOPO_V3:batiment`. GPKG `https://data.geopf.fr/telechargement/resource/BDTOPO` | 2154 | **Verified attributes.** Roads: `largeur_de_chaussee`, `nombre_de_voies`, `position_par_rapport_au_sol`. Buildings: `hauteur`, `nombre_d_etages`, `altitude_maximale_toit`. Roads are centrelines (no polygons). |

### 1.10 Spain (IGN/CNIG). Licence "compatible CC BY 4.0" (scne.es)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | MDT02, 2nd coverage | **2 m**, national | CC BY 4.0 compatible | CNIG Centro de Descargas `https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-terreno-mdt02-segunda-cobertura` (COG, 8,308 files) | ETRS89 UTM 29/30/31 (258xx); Canarias 4083 | **No API.** Plain curl gets an empty reply. |
| DEM | MDT50cm, 3rd coverage (PNOA-LiDAR 2022–25) | **0.5 m**, partial (~45%, estimated) | same | `https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-terreno-mdt50cm` (e.g. `MDT50CM-ETRS89-H29-0526-6-8-COB3-V1.tif`) | same | V1 is provisional |
| DEM | MDT05 via WCS | 5 m max | CC BY 4.0 | WCS 2.0.1 `https://servicios.idee.es/wcs-inspire/mdt`, coverages `Elevacion25830_5`, `Elevacion4258_5`, `Elevacion4083_5` | 25830/4258/4083 | WCS does not serve 2 m or 0.5 m |
| ORTHO | **PNOA Máxima Actualidad** | ~25 cm (15 cm in places, unverified) | CC BY 4.0 | WMS `https://www.ign.es/wms-inspire/pnoa-ma`, WMTS `https://www.ign.es/wmts/pnoa-ma`, layer `OI.OrthoimageCoverage` (GoogleMapsCompatible) | 258xx, 3857, 4326 | COG download per MTN25 sheet. Urban true-ortho at 15/25 cm. |
| TOPO | IGR Redes de Transporte | ~1:25k | CC BY 4.0 | WFS `https://servicios.idee.es/wfs-inspire/transportes` (`tn-ro:RoadLink`, `tn-ro:NumberOfLanes`) | 4258/258xx | Lane count only, no width or road areas |
| TOPO | Catastro INSPIRE Buildings | cadastral | CC BY 4.0 | WFS `http://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx`. ATOM per municipality (unverified) | 258xx | `numberOfFloorsAboveGround`. Excludes País Vasco and Navarra. Host unreachable during testing. |

### 1.11 Portugal (DGT)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **LiDAR MDT-50cm** (2024–25, 10 pts/m²) | **0.5 m**, 1 km tiles | CC BY 4.0 per SNIG. **STAC says "proprietary"**, so confirm before redistributing. | STAC (public listing) `https://cdd.dgterritorio.gov.pt/dgt-be/v1/collections/MDT-50cm/items`. Asset download redirects to a **free login**. Also MDT-2m, MDS and LAZ. | EPSG:3763 (PT-TM06) | The northwest zone is still unavailable. bbox filter ignored on GET. |
| ORTHO | Ortofotos 2025 RGB+NIR | 25 cm | CC BY 4.0 (SNIG) | STAC `…/collections/ORTOS-2025/items` (login). WMS `https://cartografia.dgterritorio.gov.pt/wms/ortos2025` (unverified, timed out) | 3763 | – |
| TOPO | CartTop / BDNC 1:10k | 1:10k | unverified | **no open national download found** | 3763 | The spec has `alturaEdificio`, `larguraViaRodov` and road-area polygons, but no open release yet. Use OSM. |

### 1.12 United Kingdom (OGL v3 = Open Government Licence)

| Region | Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|---|
| England | DEM | **EA LIDAR Composite DTM 2022** | 1 m (also 2 m), 5 km OS tiles | OGL v3 | WCS `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`, coverage `13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m`. Tile search (undocumented): POST a GeoJSON polygon to `https://environment.data.gov.uk/backend/catalog/api/tiles/collections/survey/search`, which returns zip URLs like `https://environment.data.gov.uk/tiles/collections/survey/lidar_composite_dtm/2022/1/SP4500` | EPSG:27700, ODN | ~99% of England. National LIDAR Programme 1 m on the same API. |
| Scotland | DEM | Scottish Public Sector LiDAR Phases 1–6 + National LiDAR Programme | 1 m (Ph1–2) / **0.5 m** (Ph3–6, NLP) | OGL v3 | **Public S3, no auth:** `https://srsp-open-data.s3.eu-west-2.amazonaws.com/lidar/{phase-1..6,national-lidar-programme}/dtm/27700/gridded/` (e.g. `NG2004_50cm_DTM_ScotlandNationalLiDAR.tif`). WMS `https://ows.remotesensing.data.gov.scot/geoserver/scotland/wms` | 27700 | Patchwork. NLP has only about 6k one-km tiles. |
| Wales | DEM | Welsh Gov LiDAR 2020–23 | 1 m, national | OGL | **One national COG:** `https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dtm_32bit_cog.tif` (48.6 GB, read with `/vsicurl/`). Index WFS `https://datamap.gov.wales/geoserver/ows?service=WFS&typeNames=geonode:welsh_government_lidar_tile_catalogue_2020_2023` | 27700 | – |
| England | ORTHO | EA Vertical Aerial Photography | 0.2–0.4 m | OGL (unverified for VAP) | same DSP tile API | 27700 | Patchy and old (2008–14) |
| UK | ORTHO | – | – | – | – | – | **No open current orthophoto** for England, Scotland or Wales. APGB/Getmapping are not open. |
| UK | TOPO | OS OpenMap Local, OS Open Roads, Open Zoomstack, Open USRN | street level | OGL | `https://api.os.uk/downloads/v1/products` | 27700 | Building footprints without height; road centrelines without width. MasterMap Topography, Highways and Building Height are premium. |

### 1.13 Ireland (Republic; Northern Ireland noted)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | GSI Open Topographic LiDAR (OPW/TII/GSI/councils) | 2 m (OPW, TII), 1 m (GSI), 0.25 m (Westmeath), 0.125 m (Cork) | CC BY 4.0 (attribution string per tile) | FeatureServers with `DATA_URL` zips, e.g. `https://gsi.geodata.gov.ie/server/rest/services/Lidar/IE_GSI_LiDAR_Coverage_OPW_IE26_ITM/FeatureServer/3` (also GSI_Phase2/12, GSI_DCHG_DP/0, TII/0) | EPSG:2157 (ITM) | **Patchy** (rivers, coasts and road corridors, 2006–18). National programme status unverified. |
| ORTHO | – | – | – | none open found (Tailte Éireann orthos are not open; unverified) | – | – |
| TOPO | **Tailte Éireann HVD "Ways"**; Buildings | Prime2-derived | **CC BY 4.0** | `https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services/Ways/FeatureServer/9` (618k polygons, max 2000 per query); `…/Buildings/FeatureServer/0` | 2157 | **Real road-surface polygons** (no attributes). Buildings have no heights. |
| DEM (Northern Ireland) | OSNI River Basin LiDAR 2012 | 1 m, partial | Open Government Data Licence | OpenDataNI | Irish Grid (unverified) | Not a supported product |

### 1.14 Czechia (ČÚZK; CC BY 4.0 since 2023-07-01, attribution "ČÚZK, [rok]")

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **DMR 5G** (thinned ALS ground points / TIN) | points; REST image resampled to **2 m**; σz 0.18 m | CC BY 4.0 | LAZ ATOM: `https://openzu.cuzk.gov.cz/opendata/DMR5G/epsg-5514/{SM5}.zip` or `…/epsg-3045/{Ekm}_{Nkm}.zip` (2×2 km). Feeds `https://atom.cuzk.gov.cz/DMR5G-SJTSK/DMR5G-SJTSK.xml`. Raster: `https://ags.cuzk.gov.cz/arcgis2/rest/services/dmr5g/ImageServer/exportImage?bbox=…&bboxSR=5514&format=tiff&pixelType=F32&f=json` | EPSG:5514 + Bpv (5705) or 3045 | **The `…/dmr5g/ImageServer/WCSServer` endpoint returns 400** (WCS not enabled). You grid the ground points yourself. |
| DEM | DMR 4G | 5 m | CC BY 4.0 | INSPIRE WCS `https://ags.cuzk.cz/arcgis2/services/INSPIRE_Nadmorska_vyska/ImageServer/WCSServer` | 5514/3045 | Fallback. DMP 1G (DSM LAZ) is available for building heights. |
| ORTHO | Ortofoto ČR | **12.5 cm** (since 2021) | CC BY 4.0 | WMS `https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer`. WMTS (Web Mercator) `https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO_WM/MapServer/WMTS/…`. ATOM JPEG per sheet `https://openzu.cuzk.gov.cz/opendata/ORTOFOTO/WRTO24.2025.BENE09.zip` | 5514/3045/3857 | Half the country is re-flown each year |
| TOPO | ZABAGED (GPKG) | 0.5–1 m | CC BY 4.0 | `https://openzu.cuzk.gov.cz/opendata/ZABAGED-GPKG/epsg-5514/ZABAGED-5514-gpkg-20261004.zip` (dated name; resolve via ATOM) | 5514/3045 | **Road centrelines, no width.** Buildings have no height. |
| TOPO | RÚIAN | – | CC BY 4.0 | ATOM `https://services.cuzk.cz/atom-index/RUIAN-S-K-U/5514`; SHP `https://services.cuzk.gov.cz/shp/obec/epsg-5514` | 5514 | `PocetPodlazi` (floors, includes underground; noisy) |

### 1.15 Slovenia (GURS / ARSO)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **ARSO LiDAR 2011–15 DMR1** | 1 m ASCII grid, 1×1 km | **unverified** (free download, no licence text found) | `https://gis.arso.gov.si/lidar/dmr1/b_{blok}/D96TM/TM1_{Ekm}_{Nkm}.asc` (block from the fishnet at `https://gis.arso.gov.si/evode/profile.aspx?id=atlas_voda_Lidar@Arso`). LAZ `…/lidar/gkot/laz/b_{blok}/D96TM/TM_{E}_{N}.laz` | EPSG:3794 (D96/TM) | No WCS |
| DEM | GURS CLSS 2023–25 (cyclic laser scanning) | DMR as **LAZ ground points** (0.5 m raster not confirmed) | GURS general terms CC BY 4.0 (CLSS pages state none) | Viewer `https://clss.si`, 1 km tiles. Limits: 10 tiles per request, 20 downloads per day. Bulk by email to gic.gu@gov.si. **No API.** | D96/TM | Whole country since 2026-06-03 |
| DEM | GURS DMV 5 m | 5 m | CC BY 4.0 | JGP `https://ipi.eprostor.gov.si/jgp/` | 3794 | Fallback |
| ORTHO | DOF025 | 25 cm (DOF050 0.5 m) | CC BY 4.0 | WMS `https://ipi.eprostor.gov.si/wms-si-gurs-dts/wms` (`SI.GURS.ZPDZ:DOF025`, `DOF025_3857`). WMTS `https://ipi.eprostor.gov.si/gwc-si-gurs-dts/service/wmts` (KVP only) | 3794 (+3857) | No 10 cm product found |
| TOPO | **DTM – Zbirka topografskih podatkov** 1:5000 | 1:5k | CC BY 4.0 | JGP API `https://ipi.eprostor.gov.si/jgp-service-api/display-views/groups/85/composite-products/347/file?filterParam=DRZAVA&filterValue=1` (transport), `…/342/…` (buildings) | 3794 | **"Cestna površina" road-surface polygons.** Attributes unverified. |
| TOPO | Kataster nepremičnin (KN) buildings | – | CC BY 4.0 | WFS `https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs`, `SI.GURS.KN:STAVBE` | 3794 | `STEVILO_ETAZ`; `VISINA_H1/H2/H3` are absolute elevations (meaning unverified; mixed datums) |
| TOPO | ZK GJI roads | – | CC BY 4.0 | WFS `https://ipi.eprostor.gov.si/wfs-si-gurs-kgi/wfs`, `SI.GURS.KGI:POLIGONI_CESTE_G` | 3794 | Road polygons; completeness unverified |

### 1.16 Poland (GUGiK; "free, any use" since 2020-07-31, no named licence)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **NMT** | **1 m** national; **0.5 m** sheets in places; 5 m sheets mixed in | free, any use | WCS 2.0.1 `https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel` (`DTM_PL-EVRF2007-NH`). The `…/DigitalTerrainModelFormatTIFF` endpoint serves **KRON86 only**. Sheets: WFS index `https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelTerenuEVRF2007/WFS/Skorowidze` (`url_do_pobrania` → `https://opendata.geoportal.gov.pl/NumDaneWys/NMT/…asc`) | WCS EPSG:2180; sheets 2180 or 2176–2179; EVRF2007 (9651) | WCS axis order is y,x. Services are slow at times. DSM: `…/NMPT/GRID1/WCS/DigitalSurfaceModel`. |
| ORTHO | **Ortofotomapa** | **5 cm urban**, 10 cm rural, 25 cm ARiMR | free, any use | WMTS `https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution` (>10 cm) and `…/HighResolution` (≤10 cm); `…/ORTO/WMS/TrueOrtho`. GeoTIFF sheets via WFS index `…/ORTO/WFS/Skorowidze` | 2180 / 4326 (Standard also 3857) | **The HighResolution terms forbid automated harvesting.** Use sheet downloads for bulk. |
| TOPO | **BDOT10k** | 1:10k | free, any use | `https://opendata.geoportal.gov.pl/bdot10k/schemat2021/GPKG/{woj}/{TERYT4}_GPKG.zip` (also GML and SHP) | 2180 | Road lines with **`szerokoscNawierzchni`** (carriageway width in m, mandatory), carriageway count, surface material. Area polygons only for motorways and expressways. Buildings have `liczbaKondygnacji` (floors). |
| TOPO | 3D buildings (CityGML) | LoD1 2024 national; LoD2 in 236 counties | free, any use | Geoportal "Pobierz dane" → Modele 3D budynków (direct URL pattern unverified) | 2180 + EVRF2007 | LoD1 includes `measuredHeight` |
| TOPO | BDOT500 | 1:500 | **not open** as vectors | view-only WMS | – | – |

### 1.17 Netherlands (PDOK; AHN is CC0)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **AHN4 DTM** (national, 2020–22) | **0.5 m** | CC0 | WCS `https://service.pdok.nl/rws/ahn/wcs/v1_0` (`dtm_05m`, `dsm_05m`). ATOM `https://service.pdok.nl/rws/ahn/atom/dtm_05m.xml` → COG `https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/atom/downloads/dtm_05m/M_{SHEET}.tif` | EPSG:28992 + NAP (7415) | The PDOK WCS is still AHN4 |
| DEM | AHN5 (2023–24) / AHN6 (2025+) | 0.5 m | CC0 | `https://basisdata.nl/hwh-ahn/AHN5_KM/02a_DTM_50cm/AHN5_M_{xmin}_{ymin}.TIF` (1 km tiles). AHN6 `https://basisdata.nl/hwh-ahn/AHN6/02a_DTM_50cm/AHN6_2025_M_{xmin}_{ymin}.TIF`. Index `https://basisdata.nl/hwh-ahn/AUX/bladwijzer.gpkg` | 7415 | Range requests OK. Missing tiles return 403. National AHN5/6 coverage unverified. |
| ORTHO | **PDOK Luchtfoto RGB** | **8 cm** (`orthoHR`) / 25 cm, yearly | CC BY 4.0 | WMTS `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/{Actueel_orthoHR\|Actueel_ortho25\|2025_orthoHR}/{EPSG:3857\|EPSG:28992}/{z}/{x}/{y}.jpeg` | 28992, 3857, … | Yearly layers 2016–2026 |
| TOPO | **BGT** | carriageway polygons (`wegdeel`, functie "rijbaan…", `fysiek_voorkomen` = surface) | CC0 | OGC API `https://api.pdok.nl/lv/bgt/ogc/v1/collections/wegdeel/items`. Full extract `https://api.pdok.nl/lv/bgt/download/v1_0/full/custom` | 28992 | **Filter `eind_registratie IS NULL`**, because the API returns ended versions too |
| TOPO | **3DBAG** v20250903 | LoD1.2/1.3/2.2; `b3_h_maaiveld`, `b3_h_70p`, `b3_h_max`, `b3_h_nok` | CC BY 4.0 ("© 3DBAG by tudelft3d and 3DGI") | WFS `https://data.3dbag.nl/api/BAG3D/wfs`. GPKG `https://data.3dbag.nl/v20250903/3dbag_nl.gpkg.zip` (19.6 GB). Tile index `…/tile_index.fgb` | 28992 / 7415 | – |

### 1.18 Belgium

| Region | Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|---|
| Flanders | DEM | DHMV II DTM (LiDAR 2013–15) | 1 m | Modellicentie gratis hergebruik (open, attribution) | WCS `https://geo.api.vlaanderen.be/DHMV/wcs` (`DHMVII_DTM_1m`, `DHMVII_DSM_1m`) | EPSG:31370 (also 3812/4326/3857), TAW | Data is 10+ years old |
| Flanders | ORTHO | Orthofotomozaïek middenschalig winter, most recent (2025) | 15 cm | same | WMTS (KVP only) `https://geo.api.vlaanderen.be/OMWRGBMRVL/wmts`, layer `omwrgbmrvl`. WMS `…/OMWRGBMRVL/wms` | GoogleMapsVL / 31370 / 3812 | Covers Brussels too |
| Flanders | TOPO | **GRB** (`WBN` wegbaan = **carriageway polygons**, `GBG` buildings); **3D GRB** (LoD1 heights) | large scale | same | WFS `https://geo.api.vlaanderen.be/GRB/wfs`. OGC API `https://geo.api.vlaanderen.be/GRB/ogc/features/v1/collections/WBN/items`. 3D GRB `https://download.vlaanderen.be/Producten/Detail?id=971&title=3D_GRB` | 31370 | – |
| Wallonia | DEM | MNT LiDAR 2021–22 | **0.5 m** (+1 m) | CC BY 4.0 | ATOM `https://geoservices.wallonie.be/geotraitement/spwdatadownload/get/a004e570-99d6-4fe5-b83d-49b774409278/atom_service.xml` (province zips, no range support). The 1 m version is EPSG:3812. | 0.5 m in 31370, 1 m in 3812 | No WCS |
| Wallonia | ORTHO | ORTHO_LAST (spring 2026) | 25 cm | CC BY 4.0 | WMTS `https://geoservices.wallonie.be/arcgis/rest/services/IMAGERIE/ORTHO_LAST/MapServer/WMTS/1.0.0/WMTSCapabilities.xml`. WMS `…/services/IMAGERIE/ORTHO_LAST/MapServer/WMSServer` | 31370 | Yearly services from 2016 |
| Wallonia | TOPO | **PICC vDIFF** (road edges, Voirie surfaces, buildings); Bâtiments 3D 2013–14 | <25 cm | CC BY 4.0 | FileGDB `https://geoservices.wallonie.be/geotraitement/spwdatadownload/results/b795de68-726c-4bdf-a62a-a42686aa5b6f/PICC_vDIFF_FILEGDB_31370.zip`. REST `…/TOPOGRAPHIE/PICC_VDIFF/MapServer` | 31370 | Road edges are lines; build polygons from them |
| Brussels | DEM / ORTHO / TOPO | UrbIS: DTM 2019, Ortho 2024, vector `StreetSurfaces` / `Buildings` | resolutions unverified | CC0 | WMS `https://geoservices-urbis.irisnet.be/geoserver/urbisgrid/ows`. WMTS `https://geoservices-urbis.irisnet.be/geowebcache/service/wmts/rest/Ortho/…`. WFS `https://geoservices-urbis.irisnet.be/geoserver/urbisvector/wfs` | 31370 | `StreetSurfaces` is probably the whole street space (unverified). No building heights. |

---

## 2. North America

### 2.1 USA (public domain)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **3DEP dynamic ImageServer** (best available) | 1 m where lidar exists | PD | `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?bbox=..&bboxSR=4326&size=..&format=tiff&pixelType=F32&f=json` (max 8000², WMS and WCS also enabled) | 3857 service, any outSR | **Easiest route for a whole AOI** |
| DEM | 3DEP 1 m DEM tiles (per project) | 1 m, 10×10 km | PD | TNM Access `https://tnmaccess.nationalmap.gov/api/v1/products?datasets=Digital%20Elevation%20Model%20(DEM)%201%20meter&bbox=W,S,E,N&outputFormat=JSON` → `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/…tif` | NAD83 UTM + NAVD88 | Not seamless across projects |
| DEM | **S1M seamless 1 m** (new, 2026) | 1 m | PD | TNM dataset `Seamless 1-m DEM (S1M)` → `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/S1M/…` | Conus Albers (6350) + NAVD88 | Limited coverage so far |
| DEM | 1/3″ seamless; EPQS point query | ~10 m; native | PD | `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/…`; EPQS `https://epqs.nationalmap.gov/v1/json?x=LON&y=LAT&units=Meters&wkid=4326`; Planetary Computer `3dep-seamless` | NAD83 | Fallback. Lidar EPT at `s3://usgs-lidar-public`. |
| ORTHO | **NAIP via USGS ImageServer** | 0.6 m (4-band) | PD | `https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/exportImage?…&format=jpg&f=json` (max 4000²) | 3857 | **NAIP Plus** (`…/USGSNAIPPlus/ImageServer`) goes to 15 cm where states contributed |
| ORTHO | USGS Imagery Only tiles | **only to z16** (~2.4 m) | PD | `https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}` | 3857 | Too coarse. NAIP S3 buckets are requester-pays. |
| TOPO | No national road polygons. TNM NTD centrelines; city planimetrics (e.g. NYC Roadbed `i36f-5ih7`, Pavement Edge `x9uq-u3qs`) | – | PD / city terms | TNM Access; `https://data.cityofnewyork.us/resource/<id>.geojson` | local | City by city |
| TOPO | Buildings: MS GlobalML (heights) / lidar nDSM | – | CDLA-Permissive-2.0 | see §4.3 | – | – |

### 2.2 Canada (Open Government Licence – Canada)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **HRDEM Mosaic** (DTM + DSM) | 1 m / 2 m | OGL-Canada 2.0 | STAC `https://datacube.services.geo.ca/stac/api/collections/hrdem-mosaic-1m/items?bbox=…` → COG `https://canelevation-dem.s3.ca-central-1.amazonaws.com/hrdem-mosaic-1m/…-dtm.tif`. WCS 1.1.1 `https://datacube.services.geo.ca/wrapper/ogc/elevation-hrdem-mosaic?service=wcs&request=GetCapabilities` | EPSG:3979 + CGVD2013 | **500 km tiles**, so read windows. Lidar areas only. The old `/ows/elevation` URL redirects (308). |
| DEM | HRDEM per project; MRDEM-30 | 1–2 m; 30 m | OGL-Canada | STAC `hrdem-lidar`, `mrdem-30` | 3979 | Fallback |
| ORTHO | No national open high-resolution ortho; **Québec Imagerie orthorectifiée** | 20 cm (QC) | CC BY 4.0 (QC) | `https://imagerie-telechargement.portailcartographique.gouv.qc.ca/`; per-project WMS e.g. `https://imagesgeo-atlas.mrnf.gouv.qc.ca/TERRITOIRE/Imagerie_2023_Charlevoix.xml` | NAD83(CSRS) MTM | Ontario, BC and Alberta unverified |
| TOPO | National Road Network (StatCan); CanVec | centrelines | OGL-Canada | `https://geo.statcan.gc.ca/nrn_rrn/on/nrn_rrn_on_GPKG.zip`; CanVec `http://ftp.maps.canada.ca/pub/nrcan_rncan/vector/canvec/` | NAD83(CSRS) | No road polygons |
| TOPO | **NRCan Automatically Extracted Buildings** | footprints with `heightmax/heightmin` | OGL-Canada | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/extraction/auto_building/auto_building_opti_2/auto_building_opti_2.parquet` | unverified | curl got 403 (may need a browser UA) |

## 3. Asia–Pacific

### 3.1 Japan (GSI content terms = PDL1.0, CC BY 4.0-compatible since 2025-11-20; credit "地理院タイル"; Survey Act caveats for 基盤地図情報)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **GSI DEM1A** (airborne laser) | 1 m | PDL1.0; Survey Act may require approval for derived maps (internal use needs only a credit) | PNG tiles `https://cyberjapandata.gsi.go.jp/xyz/dem1a_png/{z}/{x}/{y}.png` (z≤17). GML via `https://service.gsi.go.jp/kiban/` (free login) | XYZ 3857; GML EPSG:6668 + TP | **~46% of 3rd-order meshes** (2025-03). Heights revised 2025-04-01. |
| DEM | GSI DEM5A/5B/5C, DEM10B | 5 m / 10 m | same | `…/xyz/dem5a_png/{z}/{x}/{y}.png` (z≤15), `…/xyz/dem_png/{z}/{x}/{y}.png` (z≤14) | XYZ 3857 | Decode: h = (R·2¹⁶ + G·2⁸ + B)·0.01 m, 24-bit two's complement, 0x800000 = nodata |
| DEM | Prefectural: VIRTUAL SHIZUOKA (incl. **MMS road scans**), Tokyo point clouds, 0.5 m DEMs (Ehime, Kochi, Tochigi, Hyogo, Tottori) | points / 0.5 m | CC BY (mostly) | G空間 CKAN, e.g. `https://www.geospatial.jp/ckan/dataset/virtual-shizuoka-mw` | JGD2011 plane zones (unverified) | MMS = best road-surface source where present |
| ORTHO | GSI seamlessphoto | z18 ≈ 0.5 m/px | PDL1.0 (not Survey Act results) | `https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg` (z≤18) | XYZ 3857 | Also `…/xyz/ort/`. Credit Axelspace GRUS where satellite imagery is used. |
| TOPO | 基盤地図情報 道路縁 RdEdg (road-edge lines), 建築物 | 1:2,500 urban | GSI + Survey Act | GML via kiban login. Vector tiles `https://cyberjapandata.gsi.go.jp/xyz/optimal_bvmap-v1/{z}/{x}/{y}.pbf` (has RdEdg, RdCompt, BldA) | JGD2011 | No building heights. Vector-tile licence unverified. |
| TOPO | **Project PLATEAU** (306 cities) | buildings LOD1 (`measuredHeight`)/LOD2/LOD3; **roads `tran` LOD1 polygons (~300 cities), LOD2 carriageway/sidewalk (59), LOD3 lanes (40)** | PDL1.0 (CC BY 4.0 compatible) | Catalogue API `https://api.plateau.reearth.io/datacatalog/plateau-datasets` (CityGML, 3D Tiles, MVT URLs) | EPSG:6697 (unverified) | **Only national-scale open road polygons with heights found anywhere** |

### 3.2 New Zealand (LINZ; CC BY 4.0)

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **NZ LiDAR 1 m DEM** (national mosaic) + DSM | 1 m | CC BY 4.0 | **Open S3, anonymous:** `s3://nz-elevation`. STAC `https://nz-elevation.s3.ap-southeast-2.amazonaws.com/new-zealand/new-zealand/dem_1m/2193/collection.json` (COG per Topo50 sheet) | EPSG:2193 + NZVD2016 | Fallback: 8 m contour DEM |
| ORTHO | **LINZ aerial imagery** | **7.5–12.5 cm urban, 20–30 cm rural** | CC BY 4.0 | Open S3 `s3://nz-imagery`. STAC `https://nz-imagery.s3.ap-southeast-2.amazonaws.com/catalog.json`. Basemaps `https://basemaps.linz.govt.nz/v1/tiles/aerial/WebMercatorQuad/{z}/{x}/{y}.webp?api=KEY` (key required) | 2193 / 3857 | **Best open imagery outside Europe** |
| TOPO | NZ Road Centrelines (LDS 50329): `lane_count`, `surface` | lines | CC BY 4.0 | WFS `https://data.linz.govt.nz/services;key=KEY/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames=layer-50329` | 2193 | No road polygons layer exists |
| TOPO | NZ Building Outlines (LDS 101290) | 2D, **no height** | CC BY 4.0 | WFS `layer-101290` | 2193 | Heights = DSM − DEM |

### 3.3 Australia

| Type | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| DEM | **QLD QldDem ImageServer** (lidar plus SRTM fill) | 0.5–1 m | CC BY (QLD; QldDem's own entry unverified) | `https://spatial-img.information.qld.gov.au/arcgis/rest/services/Elevation/QldDem/ImageServer/exportImage?…&format=tiff&pixelType=F32&f=json` (max 4096×2048) | 3857 | **The most automatable Australian DEM** |
| DEM | ELVIS (state lidar DEMs, point clouds) | 0.5–5 m | per custodian (GA CC BY 4.0; VIC and QLD vary, unverified) | `https://elevation.fsdf.org.au`. Web order with an emailed link. **No documented API.** | MGA + AHD | Not scriptable |
| DEM | GA National 5 m lidar DEM | 5 m | CC BY 4.0 | WCS `https://services.ga.gov.au/gis/services/DEM_LiDAR_5m_2025/MapServer/WCSServer` (returned 403 from the test host); zips `https://elevation-direct-downloads.s3-ap-southeast-2.amazonaws.com/5m-dem/national_utm_mosaics/nationalz56_ag.zip` | GDA94 | – |
| DEM | NSW 5 m; VIC 1 m (via ELVIS) | 5 m / 1 m | unverified | `https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_5M_Elevation/ImageServer` | 3857 | – |
| ORTHO | QLD LatestStateProgram_AllUsers | high resolution | CC BY 4.0 | `https://spatial-img.information.qld.gov.au/arcgis/rest/services/Basemaps/LatestStateProgram_AllUsers/ImageServer` | 3857 | Only imagery ≥3 years old is public |
| ORTHO | NSW Imagery (SIX Maps) | to level 23 | **unclear** (CC BY 3.0 vs CC BY-ND 4.0; third-party imagery) | `https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Imagery/MapServer/tile/{z}/{y}/{x}` | 3857 | Licence unverified |
| ORTHO | VIC Vicmap aerial | – | **paid** | – | – | Not open |
| TOPO | NSW Transport Theme; buildings: MS AU footprints / GlobalML | lines; 2D | unverified; ODbL / CDLA-P-2.0 | `https://portal.spatial.nsw.gov.au/server/rest/services/NSW_Transport_Theme/FeatureServer` | GDA | No open building heights (Geoscape is commercial) |

---

## 4. Global fallbacks

"Verified" means the URL returned HTTP 200 (or 206/404 as stated) on 2026-10-13.

### 4.1 Global DEM

| Source | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| Copernicus via AWS Open Data | **COP-DEM GLO-30 Public**, 2021 release | 1″ (~30 m). Tile width: 3600 px below 50° latitude, 2400 at 50–60°, 1800 at 60–70°, 1200 at 70–80°, 720 at 80–85°, 360 at 85–90° | Copernicus WorldDEM-30 licence: free, worldwide, any use **including commercial**. Attribution required. | Anonymous HTTPS COG: `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{N\|S}{lat:02}_00_{E\|W}{lon:03}_00_DEM/Copernicus_DSM_COG_10_{N\|S}{lat:02}_00_{E\|W}{lon:03}_00_DEM.tif`. Bucket `s3://copernicus-dem-30m` (eu-central-1). `tileList.txt` lists 26,450 tiles. STAC: `copernicus-dem-30m-stac.s3.amazonaws.com`. Verified: N45_00_E011_00 returns 200 (48 MB). | EPSG:4326 horizontal, EGM2008 heights (EPSG:3855) | **DSM, not DTM**: includes buildings and trees. No tile over ocean (404, treat as 0 m). **Armenia and Azerbaijan return 404** (2021 release gap), so use GLO-90 there. Tiles are Float32 and PixelIsPoint: the first pixel's centre is on the tile's NW whole-degree corner. South row and east column are removed, so tiles do not overlap. No nodata tag. Filename code "10" = 30 m, "30" = 90 m. |
| Copernicus via AWS | **GLO-90** | 3″ (~90 m) | Copernicus licence (GLO-90 text not read; assumed the same) | `https://copernicus-dem-90m.s3.amazonaws.com/Copernicus_DSM_COG_30_{tile}_DEM/Copernicus_DSM_COG_30_{tile}_DEM.tif` (verified). 26,475 tiles. | EPSG:4326 / EGM2008 | Full coverage, including Armenia and Azerbaijan. Open-Meteo uses this dataset. |
| Copernicus Data Space Ecosystem | GLO-30/90 releases **2023_1 and 2024_1** | 30 / 90 m | Same licence. Free account needed. | CDSE S3, OData, Browser | EPSG:4326 / EGM2008 | 2023_1 adds Armenia, Azerbaijan and Moldova. 2024_1 fills Spain tiles. |
| OpenTopography | Global DEM API: COP30/COP90 (2023_1), AW3D30, NASADEM, SRTMGL1, **GEDTM30**, EU_DTM, CA_MRDEM_DTM … | Varies | Source licence plus OT terms | `https://portal.opentopography.org/API/globaldem?demtype=COP30&south=&north=&west=&east=&outputFormat=GTiff&API_Key=…` (REST, returns GeoTIFF; 401 without a key, verified) | EPSG:4326 | Free key: 50 calls per 24 h for non-academics. Keys must not be shared or embedded. Commercial software integration needs an enterprise key. Max 450,000 km² per request at 30 m. |
| Microsoft Planetary Computer | STAC `cop-dem-glo-30` / `cop-dem-glo-90` | 30 / 90 m | Copernicus licence | STAC `https://planetarycomputer.microsoft.com/api/stac/v1`. Blobs need a SAS token from the anonymous endpoint `/api/sas/v1/token/elevationeuwest/copernicus-dem`. | EPSG:4326 | 2021 data, so the same Armenia gap. |
| JAXA | **ALOS AW3D30 v4.1** (Apr 2024; Japan is v3.1) | 1″ (~30 m) | JAXA Terms of Use: free use, modification and redistribution. Credit "AW3D30 (JAXA)". **Commercial use is free but must be notified to JAXA in advance.** | Registration and login: https://www.eorc.jaxa.jp/ALOS/en/aw3d30/data/index.htm. Zips per 1×1° or 5×5°. Also on OpenTopography (`AW3D30`, key required). | ITRF97/GRS80; **EGM96** heights | DSM (PRISM optical stereo), Int16. Convert the geoid before mixing with Copernicus. |
| OpenGeoHub | **GEDTM30 v1.2** (2026) | 1″ (~30 m) | **CC BY 4.0** | One global COG (432 GB, range requests verified): `https://s3.opengeohub.org/global/dtm/v1.2/gedtm_rf_m_30m_s_20060101_20151231_go_epsg.4326.3855_v1.2.tif`. Read windows with GDAL `/vsicurl/`. Also on OpenTopography as `GEDTM30`. | EPSG:4326 + EGM2008 | **Bare-earth DTM** (ML, trained on ICESat-2 and GEDI). Covers 65°S–85°N. Labelled "Use for testing purposes only". **Best openly licensed global DTM found.** |
| Univ. Bristol / Fathom | **FABDEM V1-2** (Copernicus with forests and buildings removed) | 30 m | **CC BY-NC-SA 4.0 (non-commercial)** | https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn (10×10° zips, ~297 GiB total) | EPSG:4326 / EGM2008 (unverified) | Bare earth. Fine for a private, unpublished project; not for a published or commercial one. |
| Fathom | **FathomDEM v1-0** (Dec 2024) | 30 m | CC BY-NC-SA 4.0 | Zenodo 14511570, access must be requested | EPSG:4326 (unverified) | Bare earth. Non-commercial. |

### 4.2 Global imagery

| Source | Product | Resolution | Licence | Access (URL/service type) | CRS | Notes |
|---|---|---|---|---|---|---|
| Esri | **World Imagery** (item 10df2279f9684e4a9f6a7f08febac2a9) | Down to ~0.3 m and finer in many places (zoom ≤ 23) | **Proprietary** (Esri Master Agreement). Not open. | `https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` answers without a key (verified), but the terms require an Esri account or subscription. The new Basemap Styles and Static Basemap Tiles services need an access token: 2M free tiles per month, then $0.15 per 1,000. | EPSG:3857 | Credit: "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community" (Vantor is the former Maxar). See the verdict below. |
| ESA Copernicus | **Sentinel-2 L2A** | 10 m (RGB/NIR) | Free, full and open, including commercial use. Credit: "Contains modified Copernicus Sentinel data [Year]". | Element 84 Earth Search STAC `https://earth-search.aws.element84.com/v1`, collections `sentinel-2-l2a` and `sentinel-2-c1-l2a`. Open COGs, no auth (verified). CDSE STAC `https://stac.dataspace.copernicus.eu/v1` needs a login for downloads. | UTM per MGRS tile | Use the `visual`/TCI 8-bit COG for ground-colour sampling. Filter by cloud cover. |
| EOX | **EOxCloudless** (formerly Sentinel-2 cloudless), yearly 2016–2025 | 10 m | **2016 and 2017: CC BY 4.0. 2018–2025: CC BY-NC-SA 4.0** (commercial use needs a paid licence) | WMTS `https://tiles.maps.eox.at/wmts/1.0.0/WMTSCapabilities.xml`. Layers `s2cloudless` (2016), `s2cloudless-2017` … `s2cloudless-2025`, with `_3857` variants. Tile URL: `…/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg`. | EPSG:4326 / 3857 | Seamless and cloud-free. Use 2016/2017 for anything published. |
| ESA / VITO | **WorldCover 2021 v200** (11 land-cover classes) | 10 m | **CC BY 4.0** | `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N45E009_Map.tif` (3×3° tiles, verified) | EPSG:4326 | Ground-class masks (grass, tree, crop, built, bare, water). |

**Esri World Imagery: may it be used for tracing or colour sampling?**
- **Tracing: yes, but only narrowly.** Esri's "World Imagery Uses Permitted" document, linked from the item's Terms of Use, grants a "non-exclusive right to use the World Imagery map to trace features and validate edits". The resulting vectors may be shared as open data or in OSM under ODbL. The OSM permission dates from **August 2017**, not 2015. Sources: https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9 and https://www.arcgis.com/sharing/rest/content/items/8e90a00a0a6845a49262e0b756f57a10/data
- **Colour sampling, texture baking, caching or bulk tile download: not permitted.** Esri Master Agreement (rev. 2025-08-01):
  - §3.2(a): Data may be used only with the Esri products it was provided for.
  - §3.2(c): no scraping, downloading or storing, except Esri Content Packages.
  - §3.3(b): Data may not be used to compile, enhance or verify datasets for third parties.
  - The terms-of-use summary (2025-04-21) also bans systematic tile harvesting.
- **Practical rule:** show Esri as a live, attributed reference layer for manual tracing only. For colours and textures, use Sentinel-2 or EOxCloudless 2016/2017, or national open orthophotos (section 2). This is a reading of the terms, not legal advice.

### 4.3 Global buildings

| Source | Product | Licence | Access | Height attributes | Notes |
|---|---|---|---|---|---|
| Overture Maps | Theme `buildings`, types `building` and `building_part`. **Latest release 2026-09-23.1.** | **ODbL-1.0** (theme). Credit: "© OpenStreetMap contributors, Overture Maps Foundation". | GeoParquet: `s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*`. Azure: `https://overturemapswestus2.blob.core.windows.net/release/…`. PMTiles: `https://tiles.overturemaps.org/2026-09-23.1/buildings.pmtiles`. STAC: `https://stac.overturemaps.org/catalog.json` (read `latest`). | `height`, `min_height`, `num_floors`, `num_floors_underground`, `roof_shape`, `roof_height`, `roof_direction`, `roof_orientation`, `roof_material`, `roof_color`, `facade_color`, `facade_material` | Combines OSM, Microsoft, Google, Esri Community Maps, USGS 3DEP and others. Only the last ~3 releases stay on S3, so don't pin old versions. |
| Microsoft | Global ML Building Footprints (~1.4B footprints) | **CDLA-Permissive-2.0 since 2026-03-11** (was ODbL) | **New index: `https://bfppub.blob.core.windows.net/%24web/2026-08-13/dataset-links.csv`.** The old `minedbuildings.z5.web.core.windows.net/.../dataset-links.csv` is stale (moved 2026-07-24). GeoJSONL by zoom-9 quadkey. | `height` in metres, **-1 = no estimate**. 174M+ footprints have heights, mostly US and Europe. | Included in Overture. |
| Google | **Open Buildings v3** (1.8B footprints) | Your choice of **CC BY 4.0 or ODbL** | `https://storage.googleapis.com/open-buildings-data/v3/polygons_s2_level_4_gzip/…csv.gz`, or Earth Engine `GOOGLE/Research/open-buildings/v3/polygons` | **None** | Covers Africa, South and South-East Asia, Latin America and the Caribbean only. |
| Google | **Open Buildings 2.5D Temporal v1** (2016–2023) | CC BY 4.0 or ODbL | Earth Engine `GOOGLE/Research/open-buildings-temporal/v1`, or `gs://open-buildings-temporal-data/v1/` | Raster building **height**, ~4 m effective (MAE ~1.5 m) | Same regions as v3. |

DuckDB pattern (from Overture docs, not executed here):
```sql
INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs; SET s3_region='us-west-2';
COPY (SELECT id, height, num_floors, min_height, roof_shape, class, geometry
      FROM read_parquet('s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*', hive_partitioning=1)
      WHERE bbox.xmin BETWEEN 11.40 AND 11.60 AND bbox.ymin BETWEEN 45.50 AND 45.70)
TO 'buildings.geojson' WITH (FORMAT GDAL, DRIVER 'GeoJSON');
```

**Copernicus attribution for distributed outputs** (licence Art. 6b): "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved". Art. 6c adds a no-liability sentence, and Art. 6e requires passing these obligations on to downstream users.

---

## 5. Web API facts (verified 2026-10-13)

Method: "live" means I sent real requests from a terminal with curl on 2026-10-13 and read the response headers and bodies. "doc" means I read the official page at the linked URL that day.

### 5.1 Open-Meteo Elevation API: `https://api.open-meteo.com/v1/elevation`

| Fact | Finding | How verified |
|---|---|---|
| Max coordinates per request | **100.** A request with 101 points returns HTTP 400 and `{"error":true,"reason":"Parameter 'latitude' and 'longitude' must not exceed 100 coordinates."}`. The docs say "Up to 100 coordinates can be requested at once." | live + doc |
| DEM and resolution | **Copernicus DEM 2021 release GLO-90, 90 m.** This is a DSM, so tree canopy and buildings are included. The docs say "Data is based on the Copernicus DEM 2021 release GLO-90 with 90 meters resolution." | doc |
| Request format | `?latitude=a,b,c&longitude=x,y,z` (comma-separated WGS84). The response is `{"elevation":[...]}`. An `apikey` parameter and a `customer-api.open-meteo.com` host are for commercial plans. | live + doc |
| CORS / browser-callable | **Yes.** GET returns `access-control-allow-origin: *`. The OPTIONS preflight returns 200 with `access-control-allow-methods: GET, POST, OPTIONS` and `access-control-max-age: 600`. | live |
| Free-tier limits | Non-commercial use only: "Less than 10'000 API calls per day, 5'000 per hour and 600 per minute". The comparison table also gives 300,000 per month. Open-Meteo may block abusive IPs without notice. | doc ([terms](https://open-meteo.com/en/terms)) |
| Call weighting | **Unverified for elevation.** For the forecast APIs, Open-Meteo weights a multi-location request roughly by the number of locations, so one 100-point request may count as up to 100 calls. Plan the budget assuming that. | not confirmed |
| Licence / attribution | API data is CC BY 4.0. Users must attribute the Copernicus programme and Open-Meteo. ESA asks for DOI 10.5270/ESA-c5d3d65 in research. | doc |

Sources: https://open-meteo.com/en/docs/elevation-api, https://open-meteo.com/en/terms

Implication: GLO-90 is too coarse for a driving surface. Use it only for horizon and backdrop terrain, or as a last-resort fallback. Use national LiDAR DTM for the road corridor.

### 5.2 Open Topo Data public API: `https://api.opentopodata.org/v1/<dataset>?locations=lat,lon|lat,lon`

| Fact | Finding | How verified |
|---|---|---|
| Limits | "Max 100 locations per request", "Max 1 call per second", "Max 1000 calls per day". A second call within one second got `Per-second rate limit exceded for the free hosted API`. | doc + live |
| Datasets on the public API | `nzdem8m` (8 m NZ, LINZ), `ned10m` (~10 m CONUS/HI/parts of AK, USGS), `eudem25m` (25 m Europe, EEA), `mapzen` (~30 m global incl. bathymetry), `aster30m` (~30 m global), `srtm30m` / `srtm90m` (±60°), `bkg200m` (200 m Germany), `etopo1` (~1.8 km), `gebco2020` (~450 m), `emod2018` (~100 m EU bathymetry). Live calls succeeded for eudem25m, aster30m, srtm30m, mapzen, ned10m (null outside US), nzdem8m, emod2018, etopo1, gebco2020 and bkg200m. You can request several datasets as a fallback chain with `/v1/eudem25m,aster30m`. | doc + live |
| CORS | **Not browser-callable.** The GET response has no `Access-Control-Allow-Origin` header. The OPTIONS preflight returns 204 with allow-methods but no allow-origin. The self-hosted server's `access_control_allow_origin` setting defaults to `null`, which sends no header. Call it from a backend, or self-host it with that option set. Paid GPXZ is the CORS-enabled hosted alternative. | live + doc |
| Server version | `x-opentopodata-version: 1.9.0` | live |

Sources: https://www.opentopodata.org/, https://www.opentopodata.org/server/

### 5.3 Overpass API public instances

Test: POST `[out:json][timeout:25];node(45.7,11.9,45.701,11.901);out count;` with `Origin` and `Referer` headers, several rounds between 10:42 and 11:05 UTC on 2026-10-13.

| Instance | Result on 2026-10-13 | CORS | Notes |
|---|---|---|---|
| `https://overpass-api.de/api/interpreter` (FOSSGIS main) | **Alive but overloaded.** HTTP 200 in about 1.5 s in some rounds and HTTP 504 after 8–13 s in others. `/api/status`: "Rate limit: 4", 4 slots. Data was current (`timestamp_osm_base` 2026-10-13T10:54Z). | `Access-Control-Allow-Origin: *` (preflight OK) | **Returns 406 Not Acceptable** for curl's stock User-Agent, and also for a Chrome User-Agent sent without a Referer. Returns 200 with a custom identifying UA, or with a Chrome UA plus Referer. A real browser page sends a Referer automatically, unless `Referrer-Policy: no-referrer` is set. Retry on 504 or 429 with backoff. |
| `https://overpass.private.coffee/api/interpreter` | **Barely usable.** One HTTP 200 after **77 s**. Every other attempt timed out at 90 s. **Its data was stale**: `timestamp_osm_base` = 2026-06-01T08:52Z, about 4.5 months old, from a single sample. | `Access-Control-Allow-Origin: *` | The OSM wiki says private.coffee was formerly overpass.kumi.systems. Use it only as a last fallback, and check `osm3s.timestamp_osm_base` in each response. |
| `https://maps.mail.ru/osm/tools/overpass/api/interpreter` (VK Maps) | **Flaky.** HTTP 200 once at 10:42Z with current data. Every later attempt got HTTP 504 Gateway Time-out, both on queries and on `/api/status`. | `Access-Control-Allow-Origin: *` (also sent on the 504 responses) | The wiki policy says "There are currently no requests limitations". |
| `https://overpass.kumi.systems/api/interpreter` | **Dead in practice.** TLS connected, but no response in any of 4 attempts (90 s timeout). | – | Superseded by private.coffee. Remove it from fallback lists. |
| Others tried | `overpass.osm.jp` failed with a TLS certificate error (curl exit 60). `overpass.openstreetmap.ru` timed out. `overpass.osm.ch` returned 200, but it holds only a Switzerland extract. | – | – |

Recommendation: use overpass-api.de as primary, with an identifying UA or Referer, a 25 s server timeout and retry with backoff. Use mail.ru and then private.coffee as fallbacks. Always read `osm3s.timestamp_osm_base` and reject stale responses. For heavy use, self-host Overpass, or use Geofabrik extracts or Overture `transportation`.

Usage policy (main instance, FOSSGIS). From the [OSM wiki](https://wiki.openstreetmap.org/wiki/Overpass_API) and the [Overpass commons page](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html):
- Guideline: "less than 10,000 queries per day" and "less than 1 GB data per day". For regular use, divide those numbers by 100. For an app, the totals are summed over all its users.
- "No parallel running of multiple scripts." "Commercial use should use self-hosted or paid Overpass servers."
- Apps must send a `User-Agent` or `Referer` that uniquely identifies the app. On HTTP 429 or 406, wait 30 s before retrying.
- Rate-limit slots per IP are shown in `/api/status`. A request that exceeds them gets 429, and the server waits up to 15 s for a free slot before answering.
- The commons page lists "Setting up an app for more than just OSM mappers and relying on the public instances as backend" as problematic behaviour.
- The wiki now says: "Do not use platforms for fast-deployment of AI-generated apps like lovable.app or netlify.app."
- VK Maps (maps.mail.ru): the wiki says "There are currently no requests limitations".
- Private.coffee: the wiki says there is no rate limit, but asks you to notify them in advance for large-scale use. The wiki describes it as "formerly overpass.kumi.systems".
- Other options: NextGIS (free API key), FairwayMapper (free tier for OSM contributors). Geofabrik, Tracestrack, Overspan and Mapsource are paid.

### 5.4 Nominatim (nominatim.openstreetmap.org) usage policy

Source: https://operations.osmfoundation.org/policies/nominatim/ (doc). CORS checked live.
- **Rate:** "an absolute maximum of 1 request per second", from a single thread on one machine, with no distributed scripts. Scripts that run longer than a day or on a schedule are limited to 4 requests per minute.
- **Autocomplete is forbidden.** "Auto-complete search" is listed under unacceptable use, and the policy says "you must not implement such a service on the client side using the API". Search must run on an explicit submit, not on each keystroke.
- **Identification:** "Provide a valid HTTP Referer or User-Agent identifying the application". "stock User-Agents as set by http libraries will not do". A browser sends a Referer automatically.
- **Caching:** "Results must be cached on your side." Clients that repeat the same query may be blocked. Bulk geocoding is discouraged. Apps must be able to switch to another service if OSMF asks.
- **Attribution:** ODbL attribution is required (© OpenStreetMap contributors).
- **CORS:** `access-control-allow-origin: *` is present (live). It is browser-callable within these limits.

### 5.5 Google Maps Platform: Street View and Elevation

Sources:
- Pricing: https://developers.google.com/maps/billing-and-pricing/pricing (page "Last updated 2026-10-08")
- SKU details: https://developers.google.com/maps/billing-and-pricing/sku-details
- JS Street View guide: https://developers.google.com/maps/documentation/javascript/streetview
- Static API metadata: https://developers.google.com/maps/documentation/streetview/metadata
- Street View policies: https://developers.google.com/maps/documentation/streetview/policies
- GMP Terms: https://cloud.google.com/maps-platform/terms (current version, last modified August 26, 2026)

| Question | Answer | Evidence |
|---|---|---|
| Is `StreetViewService.getPanorama()` (Maps JS API) billed? | **No.** The JS Street View guide says: "The built-in Street View experience based on the Street View Pegman control, and the `StreetViewService`, are not billed." Its SKU list also gives only `StreetViewPanorama` and `Map.getStreetView()` as Dynamic Street View triggers. Loading the Maps JS API itself still needs a key with billing enabled. Each `google.maps.Map` load is a Dynamic Maps event (10,000 free per month, then $7.00 per 1,000). | doc |
| Is the Street View Static API **metadata** endpoint free? | **Yes.** "Street View Static API metadata requests are available at no charge." "No quota is consumed when you request metadata." The pricing page lists SKU *Street View Metadata* (Essentials) with an **Unlimited** free cap and no price. The endpoint is `https://maps.googleapis.com/maps/api/streetview/metadata?location=lat,lng` (or `pano=`) `&key=…`. It still needs an API key. | doc |
| Can pano IDs be stored indefinitely? | **Yes, legally.** The Street View policies page says "The panorama ID … is exempt from the caching restriction" and "you can store panorama ID values indefinitely". **Technically, though, IDs are not permanent.** The metadata docs say "Panoramas may change IDs over time, so don't persist this ID" and recommend storing the location to refresh it. The JS guide says a pano ID from `pano_changed` "is only stable within the current browser session", and that IDs from `StreetViewService` "are only stable for the lifetime of the imagery". Store pano ID, lat/lng and capture date together, and re-resolve by location when an ID fails. | doc |
| Dynamic Street View cost (2025–2026 model) | SKU **Dynamic Street View** (Pro, 658E-F885-E11A) is billed per panorama load, "for each instantiation of a panorama object" (`new google.maps.StreetViewPanorama()` or `Map.getStreetView()`). **Free cap: 5,000 per month.** Prices per 1,000: **$14.00** (5k–100k), $11.20 (100k–500k), $8.40 (500k–1M), $4.20 (1M–5M), $1.05 (5M+). The $200 monthly credit ended on 28 Feb 2025 (per the JS usage page). Since March 2025 each SKU has its own free monthly cap. Tip: create one `StreetViewPanorama` and move it with `setPano()`/`setPosition()`. The SKU text bills per instantiation, but whether `setPano()` on an existing object triggers another billable load is **unverified**. | doc |
| Static Street View images | Essentials. 10,000 free per month, then $7.00 per 1,000 (to 100k), $5.60, $4.20, $2.10, $0.53. | doc |
| Elevation (Google) | SKU "Elevation" is **Pro**. 5,000 free per month, then $5.00 per 1,000 (to 100k), $4.00, $3.00, $1.50, $0.38. | doc (pricing page) |

**GMP Terms restrictions that matter for a track generator** (GMP Terms §3.2.3, current version last modified August 26, 2026):
- §3.2.3(c) "No Creating Content From Google Maps Content". Its examples include "(i) trace or digitize roadways … from the Maps JavaScript API Satellite base map type" and "(iii) build terrain models based on elevation values from the Elevation API". **Google Elevation must therefore not be used to build the track terrain or road profile, and Google satellite imagery must not be traced.** Street View is allowed as visual reference only.
- §3.2.3(a) No Scraping: no bulk download of "Street View images … elevation values". The Elevation API policies also say "Content from the Elevation API should not be pre-fetched, cached, or stored" and "Elevation API results displayed on a map must be shown on a Google Map."
- §3.2.3(e) No Use With Non-Google Maps: for example, Customer will not "display Street View imagery and non-Google Maps on the same screen". **An OSM or Leaflet planner map shown next to an embedded Street View panorama in the same screen conflicts with this clause.**

**The studio's Street View image cache (user's decision, 2026-10-13).** `tools/streetview_fetch.py` downloads panoramas along a track's route and `tools/streetview_views.py` cuts survey views and road-surface patches from them. This is exactly what §3.2.3(a) forbids, and using the pixels in a track's textures also goes against §3.2.3(c); the user chose to do it for private tracks all the same. What follows from that:
- It does not use the Maps Platform API or the key. The panorama list comes from `https://www.google.com/maps/photometa/ac/v1` (all panoramas in a zoom-17 map tile: id, position, heading / pitch / roll), the capture month and native size from `https://www.google.com/maps/photometa/v1`, the image tiles from `https://streetviewpixels-pa.googleapis.com/v1/tile` (zoom 5: 32 × 16 tiles of 512 px for 16384 × 8192 panoramas, 26 × 13 for the older 13312 × 6656). These are the endpoints Google Maps' own web page uses: undocumented, unversioned, and they can change or start refusing requests at any time (the older `GeoPhotoService.SingleImageSearch` was switched off).
- Checked on 2026-10-13: the tile endpoint answers 403 to curl's default User-Agent and 400 for a tile outside the panorama's grid; the rows above and below the horizon band come at half resolution.
- The imagery is Google's (and its licensors') copyright. Keep it and everything made from it private: never in a published track, a shared zip, a screenshot posted online or a repository.
- The camera is about 2.0 m above the road (measured on 2023 and 2026 imagery by matching the road surface between neighbouring panoramas), and the panorama is in the car's frame, not levelled.

---

## 6. Open items (unverified, worth a follow-up)
- **Licences:**
  - Sweden: NVDB.
  - Slovenia: ARSO LiDAR (none stated).
  - Italy: Lombardia DBGT, FVG per-dataset, Trentino DTM resolution.
  - Portugal: STAC "proprietary" vs SNIG CC BY 4.0.
  - Australia: NSW imagery, QldDem, VIC DEM.
  - Canada: StatCan ODB.
- **Coverage:**
  - France LiDAR HD completion in 2026.
  - Netherlands AHN5/6 national coverage.
  - Spain MDT50cm (~45%).
  - Japan DEM1A after the 2026-07 expansion.
  - Ireland and NI national LiDAR programmes.
- **Endpoints:**
  - Denmark Datafordeler phase-out (reported for 2026-06-30, unconfirmed).
  - Esri legacy `services.arcgisonline.com` token-gating.
  - Germany: Hessen DGM1 tile naming; Baden-Württemberg and Saarland WMS.
  - Poland: 3D-building direct URLs.
- **APIs:**
  - Open-Meteo per-location call weighting for elevation.
  - Whether `StreetViewPanorama.setPano()` on an existing object triggers a new billable load.

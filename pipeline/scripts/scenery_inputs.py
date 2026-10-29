#!/usr/bin/env python3
"""What the Assetto Corsa stages need besides the road, the terrain and the buildings.

  output/forest_instances.npz    every planted object with its position (types, data = x, y, z,
                                 yaw, pitch, scale): trees, bushes and vines from vegetation.py
                                 (land cover + orthophoto crowns), hedges from the survey
                                 (climb_furniture.py) and along traced roads (boundaries.py).
                                 ac_trees.py keeps the plants (ac_trees.SPECIES: the planting
                                 catalogue's names, e.g. holm_oak_filler_1, olive_tree) and makes
                                 the cards and CSP 3D trees; ac_replay.py tests the cameras' sight
                                 lines against the crowns.
  output/terrain_base_final.png  the ground colour seen from mid and far distance: one colour per
                                 land-cover layer (ground_palette.json) modulated by the
                                 orthophoto (ortho_base.base_colour_map); ac_textures.py,
                                 ac_horizon.py.
  output/scenery/<name>/         the track's extras as Collada + DDS textures, read by
                                 ac_scenery.py: climb_landmarks/ (landmarks_climb.py: gates, masts,
                                 wayside shrines, memorials; collision boxes in
                                 output/climb_landmarks_collision.npz) and name_signs/ (signs.py).

Run after climb_furniture.py, the second build_terrain.py and ortho_base.py; before ac_track.py.
(It replaces the pipeline's former intermediate BeamNG level, export_beamng.py.)
"""

import shutil

import numpy as np
from PIL import Image

import boundaries
import climb_furniture
import landmarks_climb
import ortho_base
import signs
import vegetation
from build_terrain import LAYERS
from common import OUTPUT, Config


def forest(cfg):
    inst = vegetation.plant(cfg) + boundaries.build()[1] + climb_furniture.items()
    np.savez_compressed(OUTPUT / "forest_instances.npz",
                        types=np.array([i[0] for i in inst]),
                        data=np.array([i[1:] for i in inst], dtype=np.float32))
    print(f"forest_instances.npz: {len(inst):,} objects of {len({i[0] for i in inst})} types")


def ground_colour(cfg):
    index = np.load(OUTPUT / "terrain_layer_index.npy")
    ortho = OUTPUT / "terrain_base_ortho.png"
    photo = np.asarray(Image.open(ortho).convert("RGB")) if ortho.exists() else None
    base = ortho_base.base_colour_map(cfg, index, LAYERS, photo)
    Image.fromarray(base).save(OUTPUT / "terrain_base_final.png")
    print(f"terrain_base_final.png: {base.shape[1]} x {base.shape[0]} px{'' if photo is not None else ' (no orthophoto: palette only)'}")


def extras(cfg):
    out = OUTPUT / "scenery"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    landmarks_climb.build(cfg, out)
    signs.build(cfg, out)


def main():
    cfg = Config()
    forest(cfg)
    ground_colour(cfg)
    extras(cfg)


if __name__ == "__main__":
    main()

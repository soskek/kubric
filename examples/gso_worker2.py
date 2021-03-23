# Copyright 2020 The Kubric Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import pathlib

import numpy as np
import kubric as kb


# --- Some configuration values
# the region in which to place objects [(min), (max)]
SPAWN_REGION = [(-4, -4, 0), (4, 4, 3)]
# the range of velocities from which to sample [(min), (max)]
VELOCITY_RANGE = [(-4, -4, 0), (4, 4, 0)]
# the names of the KuBasic assets to use


# --- CLI arguments
parser = kb.ArgumentParser()
parser.add_argument("--min_num_objects", type=int, default=3)
parser.add_argument("--max_num_objects", type=int, default=10)
parser.add_argument("--floor_friction", type=float, default=0.3)
parser.add_argument("--camera_jitter", type=float, default=1.0)
parser.add_argument("--object_scale", type=float, default=8.0)

parser.add_argument("--assets_dir", type=str, default="gs://kubric-public/GSO")
parser.add_argument("--hdri_dir", type=str, default="gs://kubric-public/hdri_haven/4k")

parser.set_defaults(frame_end=24, frame_rate=12, width=512, height=512)

FLAGS = parser.parse_args()

# --- Common setups & resources
kb.setup_logging(FLAGS.logging_level)
kb.log_my_flags(FLAGS)
scratch_dir, output_dir = kb.setup_directories(FLAGS)
seed = FLAGS.seed if FLAGS.seed else np.random.randint(0, 2147483647)
rng = np.random.RandomState(seed=seed)
scene = kb.Scene.from_flags(FLAGS)
simulator = kb.simulator.PyBullet(scene, scratch_dir)
renderer = kb.renderer.Blender(scene, scratch_dir)

gso = kb.AssetSource(FLAGS.assets_dir)
hdris = kb.TextureSource(FLAGS.hdri_dir)


# --- Populate the scene
logging.info("Creating a large cube as the floor...")

floor = kb.Cube(scale=(100, 100, 1), position=(0, 0, -1),
                friction=FLAGS.floor_friction, static=True, background=True)
scene.add(floor)

background_hdri = hdris.create(texture_name=hdris.db.sample(random_state=rng).iloc[0]['id'])

renderer._set_ambient_light_hdri(background_hdri.filename)
renderer._set_background_hdri(background_hdri.filename)
renderer.use_denoising = False
floor.linked_objects[renderer].cycles.is_shadow_catcher = True


logging.info("Setting up the Camera...")
scene.camera = kb.PerspectiveCamera(focal_length=35., sensor_width=32,
                                    position=(7.48113, -6.50764, 5.34367))
scene.camera.position += rng.rand(3) * FLAGS.camera_jitter
scene.camera.look_at((0, 0, 0))

# --- Place random objects
num_objects = rng.randint(FLAGS.min_num_objects, FLAGS.max_num_objects)
logging.info("Randomly placing %d objects:", num_objects)

object_info = []
for i in range(num_objects):
  obj = gso.create(asset_id=gso.db.sample(random_state=rng).iloc[0]['id'], scale=FLAGS.object_scale)
  scene.add(obj)
  obj.metadata = {
      "asset_id": obj.asset_id,
  }
  kb.move_until_no_overlap(obj, simulator, spawn_region=SPAWN_REGION)
  # bias velocity towards center
  obj.velocity = (rng.uniform(*VELOCITY_RANGE) - [obj.position[0], obj.position[1], 0])
  logging.info("    Added %s", obj)


# --- Simulation
logging.info("Saving the simulator state to '%s' before starting the simulation.",
             output_dir / "scene.bullet")
simulator.save_state(output_dir / "scene.bullet")
logging.info("Running the Simulation ...")
animation, collisions = simulator.run()


# --- Rendering
logging.info("Saving the renderer state to '%s' before starting the rendering.",
             output_dir / "scene.blend")
renderer.save_state(output_dir / "scene.blend")
logging.info("Rendering the scene ...")
renderer.render()


# --- Postprocessing
logging.info("Parse and post-process renderer-specific output into per-frame numpy pickles.")
renderer.postprocess(from_dir=scratch_dir, to_dir=output_dir)

# --- Metadata
logging.info("Collecting and storing metadata for each object.")
kb.save_as_pkl(output_dir / "metadata.pkl", {
    "metadata": kb.get_scene_metadata(scene, seed=seed),
    "camera": kb.get_camera_info(scene.camera),
    "instances": kb.get_instance_info(scene),
    "events": {"collisions":  kb.process_collisions(collisions, scene)},
    "background": {
        "hdri": str(pathlib.Path(background_hdri.filename).name),
        "floor_friction": FLAGS.floor_friction,
    }
  })

kb.done()

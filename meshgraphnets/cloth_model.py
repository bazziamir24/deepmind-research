# pylint: disable=g-bad-file-header
# Copyright 2020 DeepMind Technologies Limited. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Model for DeformingPlate simulation (adapted from Cloth)."""

import sonnet as snt
import tensorflow.compat.v1 as tf

import common
import core_model
import normalization


class Model(snt.AbstractModule):
  """Model for static DeformingPlate simulation."""

  def __init__(self, learned_model, name="Model"):
    super(Model, self).__init__(name=name)
    with self._enter_variable_scope():
      self._learned_model = learned_model
      self._output_normalizer = normalization.Normalizer(
          size=3, name="output_normalizer"
      )
      self._node_normalizer = normalization.Normalizer(
          size=3 + common.NodeType.SIZE, name="node_normalizer"
      )
      # For 3D simulations:
      # relative_world_pos: 3 + norm: 1 = 4; relative_mesh_pos: 3 + norm: 1 = 4; total = 8.
      self._edge_normalizer = normalization.Normalizer(
          size=8, name="edge_normalizer"
      )

  def _build_graph(self, inputs, is_training):
    """Builds input graph without previous time step dependency."""
    # No velocity computation since DeformingPlate is static.
    velocity = tf.zeros_like(inputs["world_pos"])

    node_type = tf.one_hot(inputs["node_type"][:, 0], common.NodeType.SIZE)
    node_features = tf.concat([velocity, node_type], axis=-1)

    # Construct graph edges from mesh connectivity.
    senders, receivers = common.triangles_to_edges(inputs["cells"])
    relative_world_pos = tf.gather(inputs["world_pos"], senders) - tf.gather(inputs["world_pos"], receivers)
    relative_mesh_pos = tf.gather(inputs["mesh_pos"], senders) - tf.gather(inputs["mesh_pos"], receivers)
    edge_features = tf.concat([
        relative_world_pos,
        tf.norm(relative_world_pos, axis=-1, keepdims=True),
        relative_mesh_pos,
        tf.norm(relative_mesh_pos, axis=-1, keepdims=True)
    ], axis=-1)

    mesh_edges = core_model.EdgeSet(
        name="mesh_edges",
        features=self._edge_normalizer(edge_features, is_training),
        receivers=receivers,
        senders=senders
    )
    return core_model.MultiGraph(
        node_features=self._node_normalizer(node_features, is_training),
        edge_sets=[mesh_edges]
    )

  def _build(self, inputs):
    graph = self._build_graph(inputs, is_training=False)
    per_node_network_output = self._learned_model(graph)
    return self._update(inputs, per_node_network_output)

  @snt.reuse_variables
  def loss(self, inputs):
    """L2 loss on displacement."""
    graph = self._build_graph(inputs, is_training=True)
    network_output = self._learned_model(graph)

    # For static simulation, compute displacement directly.
    cur_position = inputs["world_pos"]
    target_position = inputs["target|world_pos"]
    target_displacement = target_position - cur_position

    target_normalized = self._output_normalizer(target_displacement)

    # Compute loss for normal nodes.
    loss_mask = tf.equal(inputs["node_type"][:, 0], common.NodeType.NORMAL)
    error = tf.reduce_sum((target_normalized - network_output) ** 2, axis=1)
    loss = tf.reduce_mean(error[loss_mask])
    return loss

  def _update(self, inputs, per_node_network_output):
    """Integrates the model output as a displacement."""
    displacement = self._output_normalizer.inverse(per_node_network_output)
    cur_position = inputs["world_pos"]
    new_position = cur_position + displacement
    return new_position

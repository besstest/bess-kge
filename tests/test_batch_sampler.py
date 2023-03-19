# Copyright (c) 2023 Graphcore Ltd. All rights reserved.

from typing import Dict

import einops
import numpy as np
import pytest
import torch
from numpy.testing import assert_equal

from besskge.batch_sampler import RandomShardedBatchSampler, RigidShardedBatchSampler
from besskge.dataset import KGDataset, Sharding
from besskge.negative_sampler import RandomShardedNegativeSampler

seed = 1234
n_entity = 500
n_relation_type = 10
n_shard = 4
n_triple = 2000
batches_per_step = 3
shard_bs = 120
n_negative = 250

np.random.seed(seed)

sharding = Sharding.create(n_entity, n_shard, seed=seed)

ns = RandomShardedNegativeSampler(
    n_negative=n_negative,
    sharding=sharding,
    seed=seed,
    corruption_scheme="h",
    local_sampling=False,
    flat_negative_format=False,
)

triples_h = np.random.randint(n_entity, size=(n_triple,))
triples_t = np.random.randint(n_entity, size=(n_triple,))
triples_r = np.random.randint(n_relation_type, size=(n_triple,))
triples = {"train": np.stack([triples_h, triples_r, triples_t], axis=1)}

ds = KGDataset(
    n_entity=n_entity,
    n_relation_type=n_relation_type,
    entity_dict=None,
    relation_dict=None,
    type_offsets=None,
    triples=triples,
    types=None,
    neg_heads=None,
    neg_tails=None,
)


def reconstruct_batch(batch: Dict[str, np.ndarray]):
    """Un-shard the batch generated by a ShardedBatchSampler"""
    reconstructed_batch = np.empty(
        (n_shard, batches_per_step * shard_bs, 3), dtype=np.int32
    )
    for processing_shard in range(n_shard):
        heads = np.array([]).astype(np.int32)
        relations = np.array([]).astype(np.int32)
        tails = np.array([]).astype(np.int32)

        heads = np.concatenate(
            [
                heads,
                sharding.shard_and_idx_to_entity[
                    processing_shard, batch["head"][:, processing_shard, :, :]
                ].flatten(),
            ]
        )
        relations = np.concatenate(
            [relations, batch["relation"][:, processing_shard, :, :].flatten()]
        )
        # Tails sampled from all shards and sent to processing_shard via all_to_all
        tails = np.concatenate(
            [
                tails,
                sharding.shard_and_idx_to_entity[
                    np.arange(n_shard)[None, :, None],
                    batch["tail"][:, :, processing_shard, :],
                ].flatten(),
            ]
        )

        reconstructed_batch[processing_shard] = np.stack(
            [heads, relations, tails], axis=1
        )

    return reconstructed_batch


@pytest.mark.parametrize("duplicate_batch", [True, False])
def test_random_bs(duplicate_batch: bool):
    np.random.seed(seed)

    bs = RandomShardedBatchSampler(
        part="train",
        dataset=ds,
        sharding=sharding,
        negative_sampler=ns,
        shard_bs=shard_bs,
        batches_per_step=batches_per_step,
        seed=seed,
        hrt_freq_weighting=False,
        duplicate_batch=duplicate_batch,
    )

    sampler = bs.get_dataloader_sampler()
    b = bs[next(iter(sampler))]
    reconstructed_batch = reconstruct_batch(b)
    # Check that reconstructed triples are in dataset
    for triple in reconstructed_batch.reshape(-1, 3):
        assert triple.tolist() in ds.triples["train"].tolist()

    if duplicate_batch:
        cutpoint = b["head"].shape[-1] // 2
        for prop in ["head", "relation", "tail"]:
            assert_equal(b[prop][:, :, :, :cutpoint], b[prop][:, :, :, cutpoint:])


@pytest.mark.parametrize("duplicate_batch", [True, False])
@pytest.mark.parametrize("shuffle", [True, False])
def test_rigid_bs(duplicate_batch: bool, shuffle: bool):
    np.random.seed(seed)

    bs = RigidShardedBatchSampler(
        part="train",
        dataset=ds,
        sharding=sharding,
        negative_sampler=ns,
        shard_bs=shard_bs,
        batches_per_step=batches_per_step,
        seed=seed,
        hrt_freq_weighting=False,
        duplicate_batch=duplicate_batch,
    )

    sampler = bs.get_dataloader_sampler(shuffle=shuffle)
    # Reconstruct all triples seen in one epoch
    filtered_triples = []
    for idx in iter(sampler):
        b = bs[idx]
        reconstructed_batch = reconstruct_batch(b)
        # Discard padding triples
        mask = einops.rearrange(
            b["triple_mask"],
            "step shard_h shard_t triple -> shard_h (step shard_t triple)",
        )
        filtered_triples.append(reconstructed_batch[mask])

    for triple in reconstructed_batch.reshape(-1, 3):
        assert triple.tolist() in ds.triples["train"].tolist()

    if duplicate_batch:
        cutpoint = b["head"].shape[-1] // 2
        for prop in ["head", "relation", "tail"]:
            assert_equal(b[prop][:, :, :, :cutpoint], b[prop][:, :, :, cutpoint:])

    triples_all = np.sort(np.vstack(filtered_triples), axis=0)
    if duplicate_batch:
        # Check that each triple is seen twice and discard one half for final comparison
        assert_equal(triples_all[::2], triples_all[1::2])
        triples_all = triples_all[::2]
    # Check that the set of filtered triples over one epoch coincides with the set of triples in dataset
    assert_equal(triples_all, np.sort(ds.triples["train"], axis=0))
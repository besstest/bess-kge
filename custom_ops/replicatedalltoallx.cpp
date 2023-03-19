// Copyright(c) 2022 Graphcore Ltd.All rights reserved.

#include <gcl/Collectives.hpp>

#include "replicatedalltoall.hpp"
#include "replicatedalltoallx.hpp"
#include <popart/graph.hpp>
#include <popart/ir.hpp>
#include <popart/region.hpp>
#include <popart/popx/devicex.hpp>
#include <popart/popx/irlowering.hpp>
#include <popart/popx/opxmanager.hpp>

namespace popart
{
  namespace popx
  {

    ReplicatedAllToAllOpx::ReplicatedAllToAllOpx(Op *op, Devicex *devicex)
        : CollectivesBaseOpx(op, devicex)
    {
      verifyOp<ReplicatedAllToAllOp>(op, {"custom.ops", "ReplicatedAllToAll", 1});
    }

    void ReplicatedAllToAllOpx::grow(snap::program::Sequence &prog) const
    {
      auto &op = getOp<ReplicatedAllToAllOp>();

      const poplar::OptionFlags &allToAllOptions = dv_p->lowering().gclOptions;

      poplar::Tensor output = gcl::allToAllCrossReplica(
          graph().getPoplarGraph(),
          getInTensor(ReplicatedAllToAllOp::getInIndex()).getPoplarTensor(),
          prog.getPoplarSequence(),
          toGclCommGroup(op.getReplicaGrouping()), debugContext("replicatedAllToAll"),
          allToAllOptions);

      setOutTensor(ReplicatedAllToAllOp::getOutIndex(), snap::Tensor{output, graph()});
    }

    InputCreatorType
    ReplicatedAllToAllOpx::getInputCreatorType(InIndex index) const
    {
      return InputCreatorType::CanUnwind;
    }

    snap::Tensor ReplicatedAllToAllOpx::unwindTensorLayout(snap::Tensor tensor,
                                                           InIndex,
                                                           OutIndex) const
    {
      return tensor;
    }

    view::RegMap ReplicatedAllToAllOpx::unwindRegion(InIndex, OutIndex) const
    {
      return [](const view::Region &r)
      { return view::Regions(1, r); };
    }

    ReplicatedAllToAllGradOpx::ReplicatedAllToAllGradOpx(Op *op, Devicex *devicex)
        : ReplicatedAllToAllOpx(op, devicex)
    {
      verifyOp<ReplicatedAllToAllGradOp>(op, {"custom.ops", "ReplicatedAllToAllGrad", 1});
    }

    namespace
    {
      OpxCreator<ReplicatedAllToAllOpx>
          ReplicatedAllToAllOpxCreator({"custom.ops", "ReplicatedAllToAll", 1});

      OpxCreator<ReplicatedAllToAllGradOpx> ReplicatedAllToAllGradOpxCreator(
          {"custom.ops", "ReplicatedAllToAllGrad", 1});
    } // namespace
  }   // namespace popx
} // namespace popart
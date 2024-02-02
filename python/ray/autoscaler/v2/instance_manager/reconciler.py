import logging
from typing import Dict, List

from ray.autoscaler.v2.instance_manager.instance_manager import InstanceManager
from ray.autoscaler.v2.instance_manager.node_provider import (
    CloudInstance,
    CloudInstanceId,
    CloudInstanceProviderError,
)
from ray.autoscaler.v2.instance_manager.ray_installer import RayInstallError
from ray.autoscaler.v2.scheduler import IResourceScheduler
from ray.core.generated.autoscaler_pb2 import NodeState

logger = logging.getLogger(__name__)


class Reconciler:
    """
    Reconciler is responsible for
        1. Reconciling the instance manager's instances with external states like
        the cloud provider's, the ray cluster's states, the ray installer's results.
        It performs "passive" status transitions for the instances (where the status
        transition should only be reflecting the external states of the cloud provider
        and the ray cluster, and should not be actively changing them)

        2. Stepping the reconciler to the next state by computing instance status
        transitions that are needed and updating the instance manager's state.
        These transitions should be "active" where the transitions have side effects
        (through InstanceStatusSubscriber) to the cloud provider and the ray cluster.

    Example:
    ```
        # Step 1: Reconcile the instance manager's instances with external states.
        Reconciler.sync_from([external states])

        # Step 2: Step the reconciler to the next state by computing instance status
        # transitions that are needed and updating the instance manager's state.
        Reconciler.step_next()

    """

    @staticmethod
    def sync_from(
        instance_manager: InstanceManager,
        ray_nodes: List[NodeState],
        non_terminated_cloud_instances: Dict[CloudInstanceId, CloudInstance],
        cloud_provider_errors: List[CloudInstanceProviderError],
        ray_install_errors: List[RayInstallError],
    ):
        """
        Reconcile the instance states of the instance manager from external states like
        the cloud provider's, the ray cluster's states, the ray installer's results,
        etc.

        For each instance, we try to figure out if we need to transition the instance
        status to a new status, and if so, what the new status should be.

        These transitions should be purely "passive", meaning they should only be
        reflecting the external states of the cloud provider and the ray cluster,
        and should not be actively changing the states of the cloud provider or the ray
        cluster.

        More specifically, we will reconcile status transitions for:
            1.  QUEUED/REQUESTED -> ALLOCATED:
                When a instance with launch request id (indicating a previous launch
                request was made) could be assigned to an unassigned cloud instance
                of the same instance type.
            2.  REQUESTED -> ALLOCATION_FAILED:
                When there's an error from the cloud provider for launch failure so
                that the instance becomes ALLOCATION_FAILED.
            3.  * -> RAY_RUNNING:
                When a ray node on a cloud instance joins the ray cluster, we will
                transition the instance to RAY_RUNNING.
            4.  * -> TERMINATED:
                When the cloud instance is already terminated, we will transition the
                instance to TERMINATED.
            5.  TERMINATING -> TERMINATION_FAILED:
                When there's an error from the cloud provider for termination failure.
            6.  * -> RAY_STOPPED:
                When ray was stopped on the cloud instance, we will transition the
                instance to RAY_STOPPED.
            7.  * -> RAY_INSTALL_FAILED:
                When there's an error from RayInstaller.

        Args:
            instance_manager: The instance manager to reconcile.
            ray_nodes: The ray cluster's states of ray nodes.
            non_terminated_cloud_instances: The non-terminated cloud instances from
                the cloud provider.
            cloud_provider_errors: The errors from the cloud provider.
            ray_install_errors: The errors from RayInstaller.

        """

        # Handle 1 & 2 for cloud instance allocation.
        Reconciler._handle_cloud_instance_allocation(
            instance_manager,
            non_terminated_cloud_instances,
            cloud_provider_errors,
        )
        Reconciler._handle_cloud_instance_terminated(
            instance_manager, non_terminated_cloud_instances, cloud_provider_errors
        )
        Reconciler._handle_ray_running(instance_manager, ray_nodes)
        Reconciler._handle_ray_stopped(instance_manager, ray_nodes)
        Reconciler._handle_ray_install_failed(instance_manager, ray_install_errors)

        pass

    @staticmethod
    def step_next(
        instance_manager: InstanceManager,
        scheduler: IResourceScheduler,
    ):
        """
        Step the reconciler to the next state by computing instance status transitions
        that are needed and updating the instance manager's state.

        Specifically, we will:
            1. Shut down extra cloud instances
              (* -> TERMINATING)
                a. Leaked cloud instances that are not managed by the instance manager.
                b. Extra cloud due to max nodes config.
                c. Cloud instances with outdated configs.
                d. Stopped ray nodes or failed to install ray nodes.
            2. Create new instances
              (new QUEUED)
                Create new instances based on the IResourceScheduler's decision for
                scaling up.
            3. Request cloud provider to launch new instances.
              (QUEUED -> REQUESTED)
            4. Install ray
              (ALLOCATED -> RAY_INSTALLING)
                When ray needs to be manually installed.
            5. Drain ray nodes
              (RAY_RUNNING -> RAY_STOPPING):
                a. Idle terminating ray nodes.
        """
        pass

    @staticmethod
    def _handle_cloud_instance_allocation(
        instance_manager: InstanceManager,
        non_terminated_cloud_instances: Dict[CloudInstanceId, CloudInstance],
        cloud_provider_errors: List[CloudInstanceProviderError],
    ):
        pass

    @staticmethod
    def _handle_ray_running(
        instance_manager: InstanceManager, ray_nodes: List[NodeState]
    ):
        pass

    @staticmethod
    def _handle_ray_stopped(
        instance_manager: InstanceManager, ray_nodes: List[NodeState]
    ):
        pass

    @staticmethod
    def _handle_ray_install_failed(
        instance_manager: InstanceManager, ray_install_errors: List[RayInstallError]
    ):
        pass

    @staticmethod
    def _handle_cloud_instance_terminated(
        instance_manager: InstanceManager,
        non_terminated_cloud_instances: Dict[CloudInstanceId, CloudInstance],
        cloud_provider_errors: List[CloudInstanceProviderError],
    ):
        pass

import logging
import os
import sys
import time

# coding: utf-8
from collections import defaultdict
from unittest.mock import MagicMock

import pytest  # noqa

import ray
from ray._private.test_utils import get_test_config_path, wait_for_condition
from ray.autoscaler._private.constants import (
    AUTOSCALER_MAX_CONCURRENT_LAUNCHES,
    AUTOSCALER_MAX_LAUNCH_BATCH,
)
from ray.autoscaler._private.fake_multi_node.node_provider import FakeMultiNodeProvider
from ray.autoscaler.v2.instance_manager.config import (
    AutoscalingConfig,
    FileConfigReader,
)
from ray.autoscaler.v2.instance_manager.node_provider import (
    LaunchNodeError,
    NodeProviderAdapter,
    ICloudInstanceProvider,
    TerminateNodeError,
    logger,
)
from ray.tests.autoscaler_test_utils import MockProvider

logger.setLevel(logging.DEBUG)


class CloudProviderTesterBase(ICloudInstanceProvider):
    def __init__(
        self,
        inner_provider: ICloudInstanceProvider,
        config: AutoscalingConfig,
    ):
        self.inner_provider = inner_provider
        self.config = config

    def shutdown(self):
        pass

    def launch(self, request_id, shape):
        self.inner_provider.launch(
            shape=shape, request_id=request_id, config=self.config
        )

    def terminate(self, request_id, ids):
        self.inner_provider.terminate(ids=ids, request_id=request_id)

    def poll_errors(self):
        return self.inner_provider.poll_errors()

    def get_non_terminated(self):
        return self.inner_provider.get_non_terminated()

    ############################
    # Test mock methods
    ############################
    def _add_creation_errors(self, e: Exception):
        raise NotImplementedError("Subclass should implement it")

    def _add_termination_errors(self, e: Exception):
        raise NotImplementedError("Subclass should implement it")


class FakeMultiNodeProviderTester(CloudProviderTesterBase):
    def __init__(self):
        self.config_reader = FileConfigReader(
            get_test_config_path("test_ray_complex.yaml"), skip_content_hash=True
        )
        self.config = self.config_reader.get_autoscaling_config()
        self.ray_session = None

        os.environ["RAY_FAKE_CLUSTER"] = "1"
        provider_config = self.config.get_provider_config()
        # This is a bit hacky but we need a fake head node.
        self.ray_session = ray.init()
        provider_config["gcs_address"] = self.ray_session.address_info["gcs_address"]
        provider_config["head_node_id"] = self.ray_session.address_info["node_id"]
        provider_config["launch_multiple"] = True
        self.base_provider = FakeMultiNodeProvider(
            provider_config,
            cluster_name="test",
        )

        provider = NodeProviderAdapter(
            self.base_provider,
        )
        super().__init__(provider, self.config)

    def get_non_terminated(self):
        nodes = self.inner_provider.get_non_terminated()
        nodes.pop(self.ray_session.address_info["node_id"], None)
        return nodes

    def shutdown(self):
        ray.shutdown()

    def _add_creation_errors(self, e: Exception):
        self.base_provider._test_add_creation_errors(e)

    def _add_termination_errors(self, e: Exception):
        self.base_provider._test_add_termination_errors(e)


class MockProviderTester(CloudProviderTesterBase):
    def __init__(self):
        self.config_reader = FileConfigReader(
            get_test_config_path("test_ray_complex.yaml"), skip_content_hash=True
        )
        self.config = self.config_reader.get_autoscaling_config()
        self.base_provider = MockProvider()
        provider = NodeProviderAdapter(
            self.base_provider,
        )
        super().__init__(provider, self.config)

    def _add_creation_errors(self, e: Exception):
        self.base_provider.creation_errors = e

    def _add_termination_errors(self, e: Exception):
        self.base_provider.termination_errors = e


class MagicMockProviderTester(CloudProviderTesterBase):
    def __init__(self, init_kwargs):
        self.config_reader = FileConfigReader(
            get_test_config_path("test_ray_complex.yaml"), skip_content_hash=True
        )
        self.config = self.config_reader.get_autoscaling_config()
        self.base_provider = MagicMock()
        provider = NodeProviderAdapter(
            self.base_provider, **init_kwargs
        )
        super().__init__(provider, self.config)

    def _add_creation_errors(self, e: Exception):
        self.base_provider.create_node_with_resources_and_labels.side_effect = e

    def _add_termination_errors(self, e: Exception):
        self.base_provider.terminate_nodes.side_effect = e


@pytest.fixture(scope="function")
def provider(request):
    if request.param == "fake_multi":
        provider = FakeMultiNodeProviderTester()
    elif request.param == "mock":
        provider = MockProviderTester()
    elif request.param == "magic_mock":
        provider = MagicMockProviderTester()
    else:
        raise ValueError(f"Invalid provider type: {request.param}")

    yield provider

    provider.shutdown()


@pytest.mark.parametrize(
    "provider",
    ["fake_multi", "mock"],
    indirect=True,
)
def test_node_providers_basic(provider):
    # Test launching.
    provider.launch(
        shape={"worker_nodes": 2},
        request_id="1",
    )

    provider.launch(
        request_id="2",
        shape={"worker_nodes": 2, "worker_nodes1": 1},
    )

    def verify():
        nodes_by_type = defaultdict(int)
        for node in provider.get_non_terminated().values():
            nodes_by_type[node.node_type] += 1
        errors = provider.poll_errors()
        print(errors)
        assert nodes_by_type == {"worker_nodes": 4, "worker_nodes1": 1}
        return True

    wait_for_condition(verify)

    nodes = provider.get_non_terminated().keys()

    # Terminate them all
    provider.terminate(
        ids=nodes,
        request_id="3",
    )

    # Launch some.
    provider.launch(
        shape={"worker_nodes": 1},
        request_id="4",
    )

    def verify():
        nodes_by_type = defaultdict(int)
        for node in provider.get_non_terminated().values():
            nodes_by_type[node.node_type] += 1

        assert nodes_by_type == {"worker_nodes": 1}
        for node in provider.get_non_terminated().values():
            assert node.request_id == "4"
        return True

    wait_for_condition(verify)


@pytest.mark.parametrize(
    "provider",
    ["fake_multi", "mock"],
    indirect=True,
)
def test_launch_failure(provider):
    provider._add_creation_errors(Exception("failed to create node"))

    provider.launch(
        shape={"worker_nodes": 2},
        request_id="2",
    )

    def verify():
        errors = provider.poll_errors()
        assert len(errors) == 1
        assert isinstance(errors[0], LaunchNodeError)
        assert errors[0].node_type == "worker_nodes"
        assert errors[0].request_id == "2"
        return True

    wait_for_condition(verify)


@pytest.mark.parametrize(
    "provider",
    ["fake_multi", "mock"],
    indirect=True,
)
def test_terminate_node_failure(provider):
    provider._add_termination_errors(Exception("failed to terminate node"))

    provider.launch(request_id="launch1", shape={"worker_nodes": 1})

    def nodes_launched():
        nodes = provider.get_non_terminated()
        return len(nodes) == 1

    wait_for_condition(nodes_launched)

    provider.terminate(request_id="terminate1", ids=["0"])

    def verify():
        errors = provider.poll_errors()
        nodes = provider.get_non_terminated()
        assert len(nodes) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], TerminateNodeError)
        assert errors[0].cloud_instance_id == "0"
        assert errors[0].request_id == "terminate1"
        return True

    wait_for_condition(verify)


@pytest.mark.parametrize(
    "provider",
    ["magic_mock"],  # Only magic mock for this.
    indirect=True,
)
def test_launch_executor_concurrency(provider):
    import threading

    launch_event = threading.Event()

    def loop(*args, **kwargs):
        launch_event.wait()

    provider.base_provider.create_node_with_resources_and_labels.side_effect = loop

    provider.launch(
        shape={
            "worker_nodes": 1,
            "worker_nodes1": 1,
        },  # 2 types, but concurrent types to launch is 1.
        request_id="1",
    )
    # Assert called only once.
    for _ in range(10):
        assert (
            provider.base_provider.create_node_with_resources_and_labels.call_count <= 1
        )
        time.sleep(0.1)

    # Finish the call.
    launch_event.set()

    def verify():
        assert (
            provider.base_provider.create_node_with_resources_and_labels.call_count == 2
        )
        return True

    wait_for_condition(verify)


if __name__ == "__main__":
    if os.environ.get("PARALLEL_CI"):
        sys.exit(pytest.main(["-n", "auto", "--boxed", "-vs", __file__]))
    else:
        sys.exit(pytest.main(["-sv", __file__]))

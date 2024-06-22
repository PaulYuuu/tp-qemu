import re

from virttest import error_context, utils_net


@error_context.context_aware
def run(test, params, env):
    """
    Get cpu mapping info from traceview session, and check
    whether it matches the vectors count.

    :params test: the test object
    :params params: the test params
    :params env: test environment

    """

    # boot the vm with the  queues
    queues = int(params["queues"])
    error_context.context(f"Boot the guest with queues = {queues}", test.log.info)

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    nic = vm.virtnet[0]
    nic_vectors = int(nic["vectors"]) if nic["vectors"] else (2 * queues + 2)
    error_context.context("Get CPU mapping info by traceview", test.log.info)
    output = utils_net.dump_traceview_log_windows(params, vm)
    check_reg = "SetupInterrruptAffinity.*?Option = 0x0"
    mapping_count = len(re.findall(check_reg, output))
    if mapping_count != nic_vectors:
        test.fail(f"Mapping info count {mapping_count} not match vectors {nic_vectors}")

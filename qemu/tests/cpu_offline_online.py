from avocado.utils import cpu
from virttest import error_context
from virttest.cpu import check_if_vm_vcpu_match


@error_context.context_aware
def run(test, params, env):
    """
    vCPU offline/online test:
    1) Launch a guest with many CPU.
    2) Offline all CPUs except 0.
    3) Online them again.
    """
    host_cpu = cpu.online_count()
    cpu_range = range(host_cpu)
    cpu_list = f"{cpu_range[1]}-{cpu_range[-1]}"
    params["smp"] = params["vcpu_maxcpus"] = host_cpu
    params["start_vm"] = "yes"
    vm = env.get_vm(params["main_vm"])
    vm.create(params=params)
    vm.verify_alive()
    session = vm.wait_for_login()

    error_context.base_context(f"Offline CPUs: {cpu_list}", test.log.info)
    session.cmd(f"chcpu -d {cpu_list}", timeout=len(cpu_range))
    if not check_if_vm_vcpu_match(1, vm):
        test.fail("CPU quantity on guest mismatch after offline")
    test.log.info(f"{cpu_list} have been offline.")

    error_context.context(f"Online CPUs: {cpu_list}", test.log.info)
    session.cmd(f"chcpu -e {cpu_list}", timeout=len(cpu_range))
    if not check_if_vm_vcpu_match(host_cpu, vm):
        test.fail("CPU quantity on guest mismatch after online again")
    test.log.info(f"{cpu_list} have been online.")

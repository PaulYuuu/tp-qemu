import os
import re

from avocado.utils import cpu, process
from virttest import env_process


def run(test, params, env):
    """
    Run kernbench for performance testing.

    1) Set up testing environment.
    2) Get a kernel code.
    3) Make the kernel with kernbench -M or time make -j 2*smp

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def download_if_not_exists():
        if not os.path.exists(file_name):
            cmd = f"wget -t 10 -c -P {tmp_dir} {file_link}"
            process.system(cmd)

    def cmd_status_output(cmd, timeout=360):
        s = 0
        o = ""
        if "guest" in test_type:
            (s, o) = session.cmd_status_output(cmd, timeout=timeout)
        else:
            cmd_result = process.run(cmd, ignore_status=True, shell=True)
            (s, o) = cmd_result.exit_status, cmd_result.stdout
        return (s, o)

    def check_ept():
        output = process.system_output("grep 'flags' /proc/cpuinfo")
        flags = output.splitlines()[0].split(":")[1].split()
        need_ept = params.get("need_ept", "no")
        if "ept" not in flags and "yes" in need_ept:
            test.cancel("This test requires a host that supports EPT")
        elif "ept" in flags and "no" in need_ept:
            cmd = "modprobe -r kvm_intel && modprobe kvm_intel ept=0"
            process.system(cmd, timeout=100, shell=True)
        elif "ept" in flags and "yes" in need_ept:
            cmd = "modprobe -r kvm_intel && modprobe kvm_intel ept=1"
            process.system(cmd, timeout=100, shell=True)

    def install_gcc():
        test.log.info("Update gcc to request version....")
        cmd = "rpm -q gcc"
        cpp_link = params.get("cpp_link")
        gcc_link = params.get("gcc_link")
        libgomp_link = params.get("libgomp_link")
        libgcc_link = params.get("libgcc_link")
        (s, o) = cmd_status_output(cmd)
        if s:
            cmd = (
                f"rpm -ivh {libgomp_link} --nodeps; rpm -ivh {libgcc_link} --nodeps; rpm -ivh {cpp_link}"
                f" --nodeps; rpm -ivh {gcc_link} --nodeps"
            )
        else:
            gcc = o.splitlines()[0].strip()
            if gcc in gcc_link:
                cmd = f"rpm -e {gcc} && rpm -ivh {gcc_link}"
            else:
                cmd = (
                    f"rpm -ivh {libgomp_link} --nodeps; rpm -ivh {libgcc_link} --nodeps; rpm -ivh"
                    f" {cpp_link} --nodeps; rpm -ivh {gcc_link} --nodeps"
                )
        (s, o) = cmd_status_output(cmd)
        if s:
            test.log.debug("Fail to install gcc.output:%s", o)

    def record_result(result):
        re_result = params.get("re_result")
        (m_value, s_value) = re.findall(re_result, result)[0]
        s_value = float(m_value) * 60 + float(s_value)
        shortname = params.get("shortname")
        result_str = f"{shortname}: {s_value}s\n"
        result_file = params.get("result_file")
        f1 = open(result_file, "a+")
        result = f1.read()
        result += result_str
        f1.write(result_str)
        f1.close()
        open(os.path.basename(result_file), "w").write(result)
        test.log.info("Test result got from %s:\n%s", result_file, result)

    test_type = params.get("test_type")
    guest_thp_cmd = params.get("guest_thp_cmd")
    cmd_timeout = int(params.get("cmd_timeout", 1200))
    tmp_dir = params.get("tmp_dir", "/tmp/kernbench/")
    check_ept()
    vm_name = params.get("main_vm", "vm1")
    cpu_multiplier = int(params.get("cpu_multiplier", 2))
    session = None
    if "guest" in test_type:
        env_process.preprocess_vm(test, params, env, vm_name)
        vm = env.get_vm(params["main_vm"])
        vm.verify_alive()
        session = vm.wait_for_login(timeout=int(params.get("login_timeout", 360)))

    # Create tmp folder and download files if need.
    if not os.path.exists(tmp_dir):
        process.system(f"mkdir {tmp_dir}")
    files = params.get("files_need").split()
    for file in files:
        file_link = params.get(f"{file}_link")
        file_name = os.path.join(tmp_dir, os.path.basename(file_link))
        download_if_not_exists()

    if "guest" in test_type:
        test.log.info("test in guest")
        vm.copy_files_to(tmp_dir, os.path.dirname(tmp_dir))
        if guest_thp_cmd is not None:
            session.cmd_output(guest_thp_cmd)
    try:
        if params.get("update_gcc") and params.get("update_gcc") == "yes":
            install_gcc()
        pre_cmd = params.get("pre_cmd")
        (s, o) = cmd_status_output(pre_cmd, timeout=cmd_timeout)
        if s:
            test.error(f"Fail command:{pre_cmd}\nOutput: {o}")

        if "guest" in test_type:
            cpu_num = params.get("smp")
        else:
            cpu_num = cpu.online_count()
        test_cmd = params.get("test_cmd") % (int(cpu_num) * cpu_multiplier)
        test.log.info("Start making the kernel ....")
        (s, o) = cmd_status_output(test_cmd, timeout=cmd_timeout)
        if s:
            test.error(f"Fail command:{test_cmd}\n Output:{o}")
        else:
            test.log.info("Output for command %s is:\n %s", test_cmd, o)
            record_result(o)
    finally:
        if params.get("post_cmd"):
            cmd_status_output(params.get("post_cmd"), timeout=cmd_timeout)
        if session:
            session.close()

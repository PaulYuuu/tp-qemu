import logging
import os
import threading
import time

from avocado.utils import process
from virttest import data_dir, error_context, remote, utils_misc, utils_test, virt_vm

LOG_JOB = logging.getLogger("avocado.test")


def format_result(result, base="12", fbase="2"):
    """
    Format the result to a fixed length string.

    :param result: result need to convert
    :param base: the length of converted string
    :param fbase: the decimal digit for float
    """
    if isinstance(result, str):
        value = "%" + base + "s"
    elif isinstance(result, int):
        value = "%" + base + "d"
    elif isinstance(result, float):
        value = "%" + base + "." + fbase + "f"
    else:
        raise TypeError(f"unexpected result type: {type(result).__name__}")
    return value % result


@error_context.context_aware
def run(test, params, env):
    """
    Virtio with qemu vhost backend with dpdk

    1) Boot up VM and reboot VM with 1G hugepages and iommu enabled
    2) Install dpdk realted packages
    3) Bind two nics to vfio-pci on VM
    4) Install and start Moongen on external host
    5) Start testpmd on VM, collect and analyze the results

    :param test: QEMU test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def _pin_vm_threads(node):
        """
        pin guest vcpu and vhost threads to cpus of a numa node repectively

        :param node: which numa node to pin
        """
        if node:
            if not isinstance(node, utils_misc.NumaNode):
                node = utils_misc.NumaNode(int(node))
            utils_test.qemu.pin_vm_threads(vm, node)

    def install_dpdk():
        """Install dpdk realted packages"""

        cmd = "yum install -y {}".format(params.get("env_pkg"))
        session.cmd(cmd, timeout=360, ignore_all_errors=True)
        session.cmd_output("rpm -qa |grep dpdk")

    def env_setup():
        """
        Prepare the test environment
        1) Set 1G hugepages and iommu enabled
        2) Copy testpmd script to guest

        """
        error_context.context("Setup env for guest")

        # setup hugepages
        session.cmd(params.get("env_hugepages_cmd"), ignore_all_errors=True)

        # install dpdk related packages
        install_dpdk()

        # install python pexpect
        session.cmd("`command -v pip pip3` install pexpect", ignore_all_errors=True)

        # copy testpmd script to guest
        testpmd_exec = params.get("testpmd_exec")
        src = os.path.join(data_dir.get_deps_dir(), f"performance/{testpmd_exec}")
        dst = f"/tmp/{testpmd_exec}"
        vm.copy_files_to(src, dst, nic_index=0)

        return dst

    def dpdk_devbind(dpdk_bind_cmd):
        """

        bind two nics to vfio-pci
        return nic1 and nic2's pci
        """

        error_context.context("bind two nics to vfio-pci")
        cmd = "modprobe vfio"
        cmd += " && modprobe vfio-pci"
        session.cmd(cmd, timeout=360, ignore_all_errors=True)
        session.cmd_output("lspci|grep Eth")
        "lspci |awk '/{}/ {{print $1}}'".format(params.get("nic_driver"))
        nic_driver = params.get("nic_driver").split()
        if len(nic_driver) > 1:
            for i in nic_driver:
                if i == "Virtio":
                    nic_pci_1 = "0000:{}".format(
                        session.cmd(f"lspci |awk '/{i} network/ {{print $1}}'").strip()
                    )
                    cmd_str = f"{dpdk_bind_cmd} --bind=vfio-pci {nic_pci_1}"
                else:
                    nic_pci_2 = "0000:{}".format(
                        session.cmd(f"lspci |awk '/{i}/ {{print $1}}'").strip()
                    )
                    cmd_str = f"{dpdk_bind_cmd} --bind=vfio-pci {nic_pci_2}"
                session.cmd_output(cmd_str)
        session.cmd_output(f"{dpdk_bind_cmd} --status")
        return nic_pci_1, nic_pci_2

    def install_moongen(session, ip, user, port, password, dpdk_bind_cmd):
        """

        Install moogen on remote moongen host

        """

        # copy MoonGen.zip to remote moongen host
        moongen_pkg = params.get("moongen_pkg")
        local_path = os.path.join(data_dir.get_deps_dir(), f"performance/{moongen_pkg}")
        remote.scp_to_remote(ip, shell_port, username, password, local_path, "/home")

        # install moongen
        cmd_str = "rm -rf /home/MoonGen"
        cmd_str += " && unzip /home/{} -d /home".format(params.get("moongen_pkg"))
        cmd_str += " && cd /home/MoonGen && ./build.sh"
        if session.cmd_status(cmd_str, timeout=300) != 0:
            test.error("Fail to install program on monngen host")

        # set hugepages
        session.cmd(params.get("generator_hugepages_cmd"), ignore_all_errors=True)

        # probe vfio and vfip-pci
        cmd_probe = "modprobe vfio; modprobe vfio-pci"
        session.cmd_status(cmd_probe, timeout=300)

        # bind nic
        moongen_dpdk_nic = params.get("moongen_dpdk_nic").split()
        for i in list(moongen_dpdk_nic):
            cmd_bind = f"{dpdk_bind_cmd} --bind=vfio-pci {i}"
            if session.cmd_status(cmd_bind) != 0:
                test.error(f"Fail to bind nic {i} on monngen host")

    def unbind_dpdk_nic(session, ip, user, port, password, dpdk_bind_cmd):
        """
        Clean the evn on Moongen host

        :param:session: remote host session
        :param:ip: remote host ip
        :param:user: remote host user
        :param:port: remote host port
        :param:password: remote host password
        :param:dpdk_bind_cmd: dpdk bind command
        """

        cmd = "pkill MoonGen ; rm -rf /tmp/throughput.log ; sleep 3"
        generator1.cmd_output(cmd)
        moongen_dpdk_nic_list = params.get("moongen_dpdk_nic")
        cmd_unbind = f"{dpdk_bind_cmd} -b ixgbe {moongen_dpdk_nic_list}"
        if session.cmd_status(cmd_unbind) != 0:
            test.error(f"Fail to unbind nic {moongen_dpdk_nic_list} on monngen host")

    def result(recode, dst):
        if os.path.getsize(dst) > 0:
            cmd = (
                f"grep -i {recode} {dst} | tail -2 | awk  -F ':'  '{{print $2}}' | head -1"
                "| awk '{print $1}'"
            )
            pps_results = process.system_output(cmd, shell=True)
            power = 10**6
            mpps_results = float(pps_results) / float(power)
            pps_results = f"{mpps_results:.2f}"
        else:
            test.error("the content of /tmp/testpmd.log is empty")

        return mpps_results

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    login_timeout = int(params.get("login_timeout", 360))

    try:
        vm.wait_for_serial_login(timeout=login_timeout, restart_network=True).close()
    except virt_vm.VMIPAddressMissingError:
        pass

    # print numa information on host and pinning vhost and vcpus to cpus
    process.system_output("numactl --hardware")
    process.system_output("numactl --show")
    _pin_vm_threads(params.get("numa_node"))
    error_context.context("Prepare env of vm/generator host", test.log.info)

    session = vm.wait_for_login(nic_index=0, timeout=login_timeout)

    vm.wait_for_get_address(0, timeout=90)
    vm.get_mac_address(1)
    vm.get_mac_address(2)

    # get parameter from dictionary
    category = params.get("category")
    params.get("pkt_size")
    kvm_ver_chk_cmd = params.get("kvm_ver_chk_cmd")
    guest_ver_cmd = params["guest_ver_cmd"]
    guest_dpdk_cmd = params["guest_dpdk_cmd"]
    record_list = params["record_list"]

    # get record_list
    record_line = ""
    for record in record_list.split():
        record_line += f"{format_result(record)}|"

    # setup env and bind nics to vfio-pci in guest

    dpdk_bind_cmd = "`command -v dpdk-devbind dpdk-devbind.py | head -1` "
    exec_file = env_setup()
    nic_pci_1, nic_pci_2 = dpdk_devbind(dpdk_bind_cmd)

    # setup env on moongen host
    generator_ip = params.get("generator")
    shell_port = params.get("shell_port_generator")
    password = params.get("password_generator")
    username = params.get("username_generator")
    generator1 = remote.wait_for_login(
        params.get("shell_client_generator"),
        generator_ip,
        shell_port,
        username,
        password,
        params.get("shell_prompt_generator"),
    )
    generator2 = remote.wait_for_login(
        params.get("shell_client_generator"),
        generator_ip,
        shell_port,
        username,
        password,
        params.get("shell_prompt_generator"),
    )
    install_moongen(
        generator1, generator_ip, username, shell_port, password, dpdk_bind_cmd
    )

    # get qemu, guest kernel, kvm version and dpdk version and write them into result
    result_path = utils_misc.get_path(test.resultsdir, "virtio_net_dpdk.RHS")
    result_file = open(result_path, "w")
    kvm_ver = process.system_output(kvm_ver_chk_cmd, shell=True).decode()
    host_ver = os.uname()[2]
    guest_ver = session.cmd_output(guest_ver_cmd)
    dpdk_ver = session.cmd_output(guest_dpdk_cmd)
    result_file.write(f"### kvm-userspace-ver : {kvm_ver}")
    result_file.write(f"### kvm_version : {host_ver}")
    result_file.write(f"### guest-kernel-ver :{guest_ver}")
    result_file.write(f"### guest-dpdk-ver :{dpdk_ver}")

    # get result tested by each scenario
    for pkt_cate in category.split():
        result_file.write(f"Category:{pkt_cate}\n")
        result_file.write("{}\n".format(record_line.rstrip("|")))
        nic1_driver = params.get("nic1_dpdk_driver")
        nic2_driver = params.get("nic2_dpdk_driver")
        whitelist_option = params.get("whitelist_option")
        cores = params.get("vcpu_sockets")
        queues = params.get("testpmd_queues")
        running_time = int(params.get("testpmd_running_time"))
        size = 60

        if pkt_cate == "rx":
            error_context.context("test guest rx pps performance", test.log.info)
            port = 1
            record = "Rx-pps"
            mac = vm.get_mac_address(1)
        if pkt_cate == "tx":
            error_context.context("test guest tx pps performance", test.log.info)
            port = 0
            record = "Tx-pps"
            mac = vm.get_mac_address(2)

        status = launch_test(
            session,
            generator1,
            generator2,
            mac,  # pylint: disable=E0606
            port,  # pylint: disable=E0606
            exec_file,
            nic1_driver,
            nic2_driver,
            whitelist_option,
            nic_pci_1,
            nic_pci_2,
            cores,
            queues,
            running_time,
        )
        if status is True:
            error_context.context(f"{pkt_cate} test is finished", test.log.info)
        else:
            test.fail("test is failed, please check your command and env")

        dst = utils_misc.get_path(test.resultsdir, f"testpmd.{pkt_cate}")
        vm.copy_files_from("/tmp/testpmd.log", dst)

        pkt_cate_r = result(f"{pkt_cate}-pps", dst)
        line = f"{format_result(size)}|"
        line += f"{format_result(pkt_cate_r)}"
        result_file.write(f"{line}\n")

    unbind_dpdk_nic(
        generator1, generator_ip, username, shell_port, password, dpdk_bind_cmd
    )

    generator1.close()
    generator2.close()
    session.close()


@error_context.context_aware
def launch_test(
    session,
    generator1,
    generator2,
    mac,
    port_id,
    exec_file,
    nic1_driver,
    nic2_driver,
    whitelist_option,
    nic_pci_1,
    nic_pci_2,
    cores,
    queues,
    running_time,
):
    """Launch MoonGen"""

    def start_moongen(generator1, mac, port_id, running_time):
        file = "/home/MoonGen/examples/udp-throughput.lua"
        cmd = f"cp {file} {file}.tmp"
        tmp_file = f"{file}.tmp"
        cmd += f" && sed -i 's/10:11:12:13:14:15/{mac}/g' {tmp_file}"
        cmd += (
            " && cd /home/MoonGen "
            f" && ./build/MoonGen {tmp_file} {port_id} > /tmp/throughput.log &"
        )
        generator1.cmd_output(cmd)

    def run_moongen_up(generator2):
        cmd = 'grep "1 devices are up" /tmp/throughput.log'
        if generator2.cmd_status(cmd) == 0:
            return True
        else:
            return False

    def start_testpmd(
        session,
        exec_file,
        nic1_driver,
        nic2_driver,
        whitelist_option,
        nic1_pci_1,
        nic2_pci_2,
        cores,
        queues,
        running_time,
    ):
        """Start testpmd on VM"""

        cmd = "`command -v python python3 | head -1` "
        cmd += f" {exec_file} {nic1_driver} {nic2_driver} {whitelist_option} {nic_pci_1} {nic_pci_2} {cores} {queues} {running_time} > /tmp/testpmd.log"
        session.cmd_output(cmd)

    moongen_thread = threading.Thread(
        target=start_moongen, args=(generator1, mac, port_id, running_time)
    )
    moongen_thread.start()

    if utils_misc.wait_for(
        lambda: run_moongen_up(generator2), 30, text="Wait until devices is up to work"
    ):
        LOG_JOB.debug("MoonGen start to work")
        testpmd_thread = threading.Thread(
            target=start_testpmd,
            args=(
                session,
                exec_file,
                nic1_driver,
                nic2_driver,
                whitelist_option,
                nic_pci_1,
                nic_pci_2,
                cores,
                queues,
                running_time,
            ),
        )
        time.sleep(3)
        testpmd_thread.start()
        testpmd_thread.join()
        moongen_thread.join()
        return True
    else:
        return False

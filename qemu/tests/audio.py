from virttest import error_context


@error_context.context_aware
def run(test, params, env):
    """
    Test guest audio:

    1) Boot guest with -soundhw ***
    2) Log into guest
    3) Write a file which contains random content to audio device, check
       whether it succeeds.
    """

    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    session = vm.wait_for_login(timeout=int(params.get("login_timeout", 360)))

    random_content_size = params.get("random_content_size")
    audio_device = params.get("audio_device")

    error_context.context("Verifying whether /dev/dsp is present")
    session.cmd(f"test -c {audio_device}")
    error_context.context("Trying to write to the device")
    session.cmd(
        f"dd if=/dev/urandom of={audio_device} bs={random_content_size} count=1"
    )

from virttest import error_context


@error_context.context_aware
def run(test, params, env):
    """
    Boot guest and query-cpu-model-expansion

    :param test: QEMU test object.
    :param params: Dictionary with test parameters.
    :param env: Dictionary with the test environment.
    :params cpu_models: expected cpu model list
    :params props: expected cpu model properties
    """
    vm = env.get_vm(params["main_vm"])
    test.log.info("Start query cpu model supported by qmp")
    # get cpu models for test
    cpu_models = params.objects("cpu_models")
    for cpu_model in cpu_models:
        args = {"type": "static", "model": {"name": cpu_model}}
        output = vm.monitor.cmd("query-cpu-model-expansion", args)
        try:
            model = output.get("model")
            model_name = model.get("name")
            model_props = model.get("props")
            if model_name != cpu_model + "-base":
                test.fail(
                    "Command query-cpu-model-expansion return"
                    " wrong model: {} with {}".format(cpu_model + "-base", model_name)
                )
            if model_name[:-5] in cpu_models:
                props = params.get_dict("props")
                keys = props.keys()
                for key in keys:
                    if props[key] == "True":
                        props[key] = True
                    elif props[key] == "False":
                        props[key] = False
                    else:
                        test.fail(
                            "unexpected values in configuration,"
                            f"key: {key}, value：{props[key]}"
                        )
                if model_props != props:
                    test.fail(
                        f"Properties {model_props} was not same as expected,{props}"
                    )
            else:
                test.fail(
                    "There is no suitable cpu model searched by expansion"
                    f"guest: {model_name}, expected: {cpu_models}"
                )
        except Exception as info:
            test.fail(info)

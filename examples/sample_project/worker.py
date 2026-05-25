def use_dynamic_import():
    # Project Doctor intentionally does not try to resolve this yet.
    module_name = "numpy"
    return __import__(module_name)

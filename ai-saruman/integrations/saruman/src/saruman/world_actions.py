"""Small, exact native interactions on Minecraft's client thread, without callbacks."""

from py4j.java_gateway import get_field


def on_client(gateway, owner, declaring_class, method_name, parameters=(), args=()):
    """Run one bound JVM method; a timeout is an unknown result, never a retry."""
    jvm = gateway.jvm
    types = gateway.new_array(jvm.java.lang.Class, len(parameters))
    for i, name in enumerate(parameters):
        types[i] = (
            jvm.java.lang.Integer.TYPE
            if name == "int"
            else jvm.java.lang.Boolean.TYPE
            if name == "boolean"
            else jvm.java.lang.Class.forName(name)
        )
    method = jvm.java.lang.Class.forName(declaring_class).getMethod(method_name, types)
    handle = (
        jvm.java.lang.invoke.MethodHandles.publicLookup()
        .unreflect(method)
        .bindTo(owner)
    )
    return _bound_on_client(gateway, handle, args)


def set_int_on_client(gateway, owner, field_name, value):
    """Bind a field setter directly; Field.set is caller-sensitive on Java 21."""
    return _set_on_client(gateway, owner, field_name, int(value))


def set_bool_on_client(gateway, owner, field_name, value):
    return _set_on_client(gateway, owner, field_name, bool(value))


def _set_on_client(gateway, owner, field_name, value):
    field = owner.getClass().getDeclaredField(field_name)
    field.setAccessible(True)
    handle = (
        gateway.jvm.java.lang.invoke.MethodHandles.publicLookup()
        .unreflectSetter(field)
        .bindTo(owner)
    )
    return _bound_on_client(gateway, handle, (value,))


def _bound_on_client(gateway, handle, args):
    jvm = gateway.jvm
    values = gateway.new_array(jvm.java.lang.Object, len(args))
    for i, value in enumerate(args):
        values[i] = value
    handle = jvm.java.lang.invoke.MethodHandles.insertArguments(handle, 0, values)
    supplier = jvm.java.lang.invoke.MethodHandleProxies.asInterfaceInstance(
        jvm.java.lang.Class.forName("java.util.function.Supplier"), handle
    )
    client = jvm.net.minecraft.class_310.method_1551()
    future = jvm.java.util.concurrent.CompletableFuture.supplyAsync(supplier, client)
    return future.get(3, jvm.java.util.concurrent.TimeUnit.SECONDS)


def interaction_manager(gateway):
    return get_field(gateway.jvm.net.minecraft.class_310.method_1551(), "field_1761")


def sync_selected_slot(gateway, slot):
    """The mod callback bypasses vanilla interaction's automatic slot sync."""
    if type(slot) is not int or not 0 <= slot < 9:
        raise ValueError("A confirmed hotbar slot is required")
    jvm = gateway.jvm
    network = jvm.net.minecraft.class_310.method_1551().method_1562()
    if network is None:
        raise RuntimeError("No server connection for equipment synchronization")
    on_client(
        gateway,
        network,
        "net.minecraft.class_8673",
        "method_52787",
        ("net.minecraft.class_2596",),
        (jvm.net.minecraft.class_2868(slot),),
    )

"""Field setters must avoid Java's caller-sensitive Field.set reflection path."""

import unittest
from unittest.mock import MagicMock

from saruman.world_actions import on_client, set_int_on_client


class WorldActionTests(unittest.TestCase):
    def test_native_callback_integer_parameter_uses_primitive_class(self):
        gateway, owner = MagicMock(), MagicMock()
        gateway.new_array.side_effect = lambda kind, count: [None] * count
        on_client(gateway, owner, "example.Callback", "attack", ("int",), (1,))
        lookup = gateway.jvm.java.lang.Class.forName.return_value.getMethod
        self.assertEqual(lookup.call_args.args[1], [gateway.jvm.java.lang.Integer.TYPE])

    def test_direct_setter_is_bound_and_submitted_once_to_client_executor(self):
        gateway, owner = MagicMock(), MagicMock()
        field = owner.getClass.return_value.getDeclaredField.return_value
        handles = gateway.jvm.java.lang.invoke.MethodHandles
        set_int_on_client(gateway, owner, "minimumFoodAllowed", 0)
        field.setAccessible.assert_called_once_with(True)
        handles.publicLookup.return_value.unreflectSetter.assert_called_once_with(field)
        handles.publicLookup.return_value.unreflect.assert_not_called()
        handles.publicLookup.return_value.unreflectSetter.return_value.bindTo.assert_called_once_with(
            owner
        )
        future = gateway.jvm.java.util.concurrent.CompletableFuture.supplyAsync
        future.assert_called_once()
        self.assertEqual(
            future.call_args.args[1],
            gateway.jvm.net.minecraft.class_310.method_1551.return_value,
        )
        future.return_value.get.assert_called_once_with(
            3, gateway.jvm.java.util.concurrent.TimeUnit.SECONDS
        )

    def test_timed_out_setter_is_never_replayed(self):
        gateway = MagicMock()
        future = gateway.jvm.java.util.concurrent.CompletableFuture.supplyAsync
        future.return_value.get.side_effect = TimeoutError("unknown result")
        with self.assertRaises(TimeoutError):
            set_int_on_client(gateway, MagicMock(), "foodUnitsToCollect", 0)
        future.assert_called_once()

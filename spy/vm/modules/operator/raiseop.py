from typing import TYPE_CHECKING

from spy.errors import SPyError
from spy.location import Loc
from spy.vm.exc import W_Exception
from spy.vm.object import W_Type
from spy.vm.opimpl import W_OpImpl
from spy.vm.opspec import W_MetaArg, W_OpSpec
from spy.vm.primitive import W_I32
from spy.vm.str import W_Str

from . import OP

if TYPE_CHECKING:
    from spy.vm.vm import SPyVM


@OP.builtin_func
def w_raise(
    vm: "SPyVM", w_etype: W_Str, w_message: W_Str, w_filename: W_Str, w_lineno: W_I32
) -> None:
    etype = vm.unwrap_str(w_etype)
    msg = vm.unwrap_str(w_message)
    w_type = vm.lookup_exc_type(etype)
    if w_type is not None:
        exc_pyclass: type[W_Exception] = w_type.pyclass  # type: ignore[assignment]
        raise SPyError.from_w_exc(exc_pyclass(msg))
    raise SPyError("W_Exception", f"{etype}: {msg}")


@OP.builtin_func
def w_raise_red(vm: "SPyVM", w_exc: W_Exception) -> None:
    raise SPyError.from_w_exc(w_exc)


@OP.builtin_func(color="blue")
def w_RAISE(vm: "SPyVM", wam_exc: W_MetaArg) -> W_OpImpl:
    from spy.vm.typechecker import typecheck_opspec

    # We are doing a bit of magic here:
    #   1. manually turn the blue wam_exc into an hardcoded message
    #   2. return an w_opimpl which calls w_raise with the hardcoded message,
    #      ignoring the actual wam_exc
    # Red raise: exception value is only known at runtime — dispatch to w_raise_red.
    if wam_exc.color == "red":
        if not issubclass(wam_exc.w_static_T.pyclass, W_Exception):
            err = SPyError("W_TypeError", "`raise` expects an exception type or instance")
            err.add("error", "this is not an exception", wam_exc.loc)
            raise err
        w_opspec = W_OpSpec(OP.w_raise_red, [wam_exc])
        return typecheck_opspec(
            vm, w_opspec, [wam_exc], dispatch="single", errmsg="cannot raise `{0}`"
        )

    w_exc = wam_exc.w_val

    # Blue raise: we support two syntaxes:
    #   raise IndexError            # raise a type
    #   raise IndexError("hello")   # raise an instance
    if isinstance(w_exc, W_Type) and issubclass(w_exc.pyclass, W_Exception):
        # exception type (both built-in and user-defined): fqn.symbol_name is always
        # the unprefixed name ("ValueError", "MyError", etc.)
        etype = w_exc.fqn.symbol_name
        msg = ""
    elif isinstance(w_exc, W_Exception):
        cls_name = w_exc.__class__.__name__
        etype = cls_name[2:] if cls_name.startswith("W_") else cls_name
        msg = w_exc.message
    else:
        err = SPyError("W_TypeError", "`raise` expects an exception type or instance")
        err.add("error", "this is not an exception", wam_exc.loc)
        raise err

    w_etype = vm.wrap(etype)
    wam_etype = W_MetaArg.from_w_obj(vm, w_etype)

    w_msg = vm.wrap(msg)
    wam_msg = W_MetaArg.from_w_obj(vm, w_msg)

    w_fname = vm.wrap(wam_exc.loc.filename)
    wam_fname = W_MetaArg.from_w_obj(vm, w_fname)

    w_lineno = vm.wrap(wam_exc.loc.line_start)
    wam_lineno = W_MetaArg.from_w_obj(vm, w_lineno)

    w_opspec = W_OpSpec(OP.w_raise, [wam_etype, wam_msg, wam_fname, wam_lineno])

    return typecheck_opspec(
        vm, w_opspec, [wam_exc], dispatch="single", errmsg="cannot raise `{0}`"
    )

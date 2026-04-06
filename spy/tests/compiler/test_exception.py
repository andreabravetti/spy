import pytest

from spy.errors import SPyError
from spy.tests.support import (
    CompilerTest,
    expect_errors,
    no_C,
    only_interp,
    skip_backends,
)
from spy.vm.exc import FrameInfo, W_Exception


class MatchFrame:
    def __init__(self, fqn: str, src: str, *, kind: str = "astframe") -> None:
        self.kind = kind
        self.fqn = fqn
        self.src = src

    def __eq__(self, info: object) -> bool:
        if not isinstance(info, FrameInfo):
            return NotImplemented
        return (
            self.kind == info.kind
            and self.fqn == str(info.fqn)
            and self.src == info.loc.get_src()
        )

    def __repr__(self) -> str:
        return f"<MatchFrame({self.fqn!r}, {self.src!r}, kind={self.kind!r})"


class TestException(CompilerTest):
    def test_try_except_basic(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("oops")
                return 1
            except ValueError:
                return -1
        """)
        assert mod.foo(1) == 1
        assert mod.foo(0) == -1

    def test_try_except_type_hierarchy(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("v")
                elif x == 1:
                    raise IndexError("i")
                return 0
            except ValueError:
                return 1
            except IndexError:
                return 2
        """)
        assert mod.foo(9) == 0
        assert mod.foo(0) == 1
        assert mod.foo(1) == 2

    def test_try_except_as(self):
        mod = self.compile("""
        def foo() -> i32:
            try:
                raise ValueError("caught me")
            except ValueError as e:
                if e == ValueError("caught me"):
                    return 42
                return -1
        """)
        assert mod.foo() == 42

    def test_exception_message_attr(self):
        mod = self.compile("""
        def foo() -> str:
            try:
                raise ValueError("hello world")
            except ValueError as e:
                return e.message
            return ""
        """)
        assert mod.foo() == "hello world"

    def test_try_except_tuple(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("v")
                elif x == 1:
                    raise IndexError("i")
                return 0
            except (ValueError, IndexError):
                return -1
        """)
        assert mod.foo(9) == 0
        assert mod.foo(0) == -1
        assert mod.foo(1) == -1

    def test_try_except_tuple_as(self):
        mod = self.compile("""
        def foo(x: i32) -> str:
            try:
                if x == 0:
                    raise ValueError("v")
                else:
                    raise IndexError("i")
            except (ValueError, IndexError) as e:
                return e.message
            return ""
        """)
        assert mod.foo(0) == "v"
        assert mod.foo(1) == "i"

    def test_try_except_unmatched_reraises(self):
        mod = self.compile("""
        def foo() -> i32:
            try:
                raise IndexError("nope")
            except ValueError:
                return -1
            return 0
        """)
        with SPyError.raises("W_IndexError", match="nope"):
            mod.foo()

    def test_bare_raise(self):
        mod = self.compile("""
        def foo() -> i32:
            try:
                raise ValueError("original")
            except ValueError:
                raise
            return 0
        """)
        with SPyError.raises("W_ValueError", match="original"):
            mod.foo()

    def test_bare_raise_preserves_type(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                try:
                    if x == 0:
                        raise ValueError("v")
                    else:
                        raise IndexError("i")
                except ValueError:
                    raise
            except IndexError:
                return 2
            return 0
        """)
        # ValueError is re-raised by the inner handler and not caught by the
        # outer except IndexError, so it propagates out.
        with SPyError.raises("W_ValueError", match="v"):
            mod.foo(0)
        # IndexError is not caught by inner handler, goes straight to outer.
        assert mod.foo(1) == 2

    def test_try_except_else(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("v")
            except ValueError:
                return -1
            else:
                return 1
            return 0
        """)
        assert mod.foo(1) == 1
        assert mod.foo(0) == -1

    def test_try_except_else_not_run_after_handler(self):
        # The else body must NOT execute when an exception was caught.
        # The handler here falls through (no return) to ensure the goto
        # after the handler would reach the else label if the fix were absent.
        mod = self.compile("""
        def foo(x: i32) -> i32:
            result: i32 = 0
            try:
                if x == 0:
                    raise ValueError("v")
            except ValueError:
                result = -1
            else:
                result = 10
            return result
        """)
        assert mod.foo(1) == 10   # no exception: else runs
        assert mod.foo(0) == -1   # exception caught: else must not run

    def test_try_finally(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("v")
                return 1
            finally:
                return -1
        """)
        assert mod.foo(1) == -1
        assert mod.foo(0) == -1

    def test_try_except_finally(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            result: i32 = 0
            try:
                if x == 0:
                    raise ValueError("v")
                result = 1
            except ValueError:
                result = -1
            finally:
                result = result * 10
            return result
        """)
        assert mod.foo(1) == 10   # no exception: result=1, finally multiplies to 10
        assert mod.foo(0) == -10  # exception caught: result=-1, finally multiplies to -10

    def test_try_except_bare(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise ValueError("v")
                elif x == 1:
                    raise IndexError("i")
                return 0
            except:
                return -1
        """)
        assert mod.foo(9) == 0
        assert mod.foo(0) == -1
        assert mod.foo(1) == -1

    def test_try_except_nested(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            try:
                try:
                    if x == 0:
                        raise ValueError("inner")
                except IndexError:
                    return -1
            except ValueError:
                return 1
            return 0
        """)
        assert mod.foo(9) == 0   # no exception
        assert mod.foo(0) == 1   # ValueError not caught by inner, caught by outer

    def test_try_except_finally_unmatched(self):
        # finally must run even when the exception is not caught
        mod = self.compile("""
        def side_effect() -> i32:
            return 99

        def foo() -> i32:
            try:
                raise ValueError("v")
            except IndexError:
                return -1
            finally:
                side_effect()
            return 0
        """)
        with SPyError.raises("W_ValueError", match="v"):
            mod.foo()

    def test_try_finally_exception_in_finally(self):
        mod = self.compile("""
        def foo() -> i32:
            try:
                raise ValueError("original")
            finally:
                raise IndexError("from finally")
            return 0
        """)
        with SPyError.raises("W_IndexError", match="from finally"):
            mod.foo()

    def test_try_finally_propagates_exception(self):
        mod = self.compile("""
        def side_effect() -> i32:
            return 99

        def foo() -> i32:
            try:
                raise ValueError("oops")
            finally:
                side_effect()
            return 0
        """)
        with SPyError.raises("W_ValueError", match="oops"):
            mod.foo()

    def test_raise(self):
        # for now, we don't support "except:", and raising an exception result
        # in a panic.
        mod = self.compile("""
        def foo(x: i32) -> i32:
            if x == 0:
                return 42
            elif x == 1:
                raise Exception("hello")   # <-- line 6
            elif x == 2:
                raise ValueError("world")
            elif x == 3:
                raise ValueError()
            else:
                raise IndexError
        """)
        assert mod.foo(0) == 42
        with SPyError.raises("W_Exception", match="hello") as excinfo:
            mod.foo(1)
        with SPyError.raises("W_ValueError", match="world"):
            mod.foo(2)
        with SPyError.raises("W_ValueError") as excinfo:
            mod.foo(3)
            assert excinfo.value.w_exc.message == ""
        with SPyError.raises("W_IndexError") as excinfo:
            mod.foo(4)
            assert excinfo.value.w_exc.message == ""

    def test_raise_red_variable(self):
        mod = self.compile("""
        def foo(x: i32) -> i32:
            var exc = ValueError("oops")
            raise exc
            return 0
        """)
        with SPyError.raises("W_ValueError", match="oops"):
            mod.foo(0)

    def test_raise_red_parameter(self):
        mod = self.compile("""
        def bar(exc: ValueError) -> i32:
            raise exc
            return 0

        def foo() -> i32:
            return bar(ValueError("from bar"))
        """)
        with SPyError.raises("W_ValueError", match="from bar"):
            mod.foo()

    def test_traceback(self):
        src = """
        def foo() -> i32:
            return bar(1)

        def bar(x: i32) -> i32:
            return baz(x, 2)

        def baz(x: i32, y: i32) -> i32:
            raise ValueError("hello")
        """
        mod = self.compile(src)
        with SPyError.raises("W_ValueError", match="hello") as exc:
            mod.foo()
        w_tb = exc.value.add_traceback()
        assert w_tb.entries == [
            MatchFrame("test::foo", "bar(1)"),
            MatchFrame("test::bar", "baz(x, 2)"),
            MatchFrame("test::baz", 'raise ValueError("hello")'),
        ]
        exc.value.w_exc.format()  # check that it doesn't fail

    @only_interp
    def test_modframe_classframe_traceback(self):
        src = """
        @blue
        def get_T():
            raise StaticError("invalid type")

        @struct
        class Point:
            x: get_T()
            y: get_T()
        """
        with SPyError.raises("W_StaticError", match="invalid type") as exc:
            mod = self.compile(src)
        w_tb = exc.value.add_traceback()
        assert w_tb.entries == [
            MatchFrame("test", "class Point:", kind="modframe"),
            MatchFrame("test::Point", "get_T()", kind="classframe"),
            MatchFrame("test::get_T", 'raise StaticError("invalid type")'),
        ]
        exc.value.w_exc.format()  # check that it doesn't fail

    def test_doppler_traceback(self):
        src = """
        @blue
        def get_k():
            raise StaticError("hello")

        def bar() -> i32:
            return get_k()

        def foo() -> i32:
            return bar()
        """
        if self.backend == "interp":
            # In [interp] we get an error ONLY when we execute foo, and the taceback is
            # foo->bar->get_k
            mod = self.compile(src)
            with SPyError.raises("W_StaticError", match="hello") as exc:
                mod.foo()

            w_tb = exc.value.add_traceback()
            assert w_tb.entries == [
                MatchFrame("test::foo", "bar()"),
                MatchFrame("test::bar", "get_k()"),
                MatchFrame("test::get_k", 'raise StaticError("hello")'),
            ]

        else:
            # In [doppler] and [C], we get an error during compilation, and traceback is
            # "redshift bar"->get_k.
            with SPyError.raises("W_StaticError", match="hello") as exc:
                self.compile(src)

            w_tb = exc.value.add_traceback()
            assert w_tb.entries == [
                MatchFrame("test::bar", "get_k()", kind="dopplerframe"),
                MatchFrame("test::get_k", 'raise StaticError("hello")'),
            ]

    def test_lazy_error(self):
        src = """
        def foo() -> None:
            1 + "hello"
        """
        mod = self.compile(src, error_mode="lazy")
        with SPyError.raises("W_TypeError", match=r"cannot do `i32` \+ `str`"):
            mod.foo()

    @pytest.mark.parametrize("error_mode", ["lazy", "eager"])
    def test_static_error(self, error_mode):
        src = """
        @blue
        def get_message(lang):
            if lang == "en":
                return "hello"
            raise StaticError("unsupported lang: " + lang)

        def print_message(also_italian: i32) -> i32:
            print(get_message("en"))
            if also_italian:
                print(get_message("it"))
            return 42

        def foo() -> i32:
            return print_message(1)
        """

        if self.backend in ("doppler", "C") and error_mode == "eager":
            # eager errors and we are redshifting: expect a comptime error
            errors = expect_errors("unsupported lang: it")
            self.compile_raises(src, "foo", errors)
        else:
            # interp mode or lazy errors
            mod = self.compile(src, error_mode=error_mode)
            assert mod.print_message(0) == 42  # works
            with SPyError.raises("W_StaticError", match="unsupported lang: it"):
                mod.print_message(1)


class TestCustomException(CompilerTest):
    def test_raise_custom(self):
        mod = self.compile("""
        class MyError(Exception):
            pass

        def foo() -> i32:
            raise MyError("oops")
            return 0
        """)
        with SPyError.raises("MyError", match="oops"):
            mod.foo()

    def test_catch_custom(self):
        mod = self.compile("""
        class MyError(Exception):
            pass

        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise MyError("bad")
            except MyError:
                return 1
            return 0
        """)
        assert mod.foo(1) == 0
        assert mod.foo(0) == 1

    def test_custom_inherits_from_builtin(self):
        mod = self.compile("""
        class MyError(ValueError):
            pass

        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise MyError("bad")
            except ValueError:
                return 1
            return 0
        """)
        assert mod.foo(1) == 0
        assert mod.foo(0) == 1  # MyError is caught by except ValueError

    def test_custom_hierarchy(self):
        mod = self.compile("""
        class Base(Exception):
            pass

        class Child(Base):
            pass

        def foo(x: i32) -> i32:
            try:
                if x == 0:
                    raise Child("child")
                raise Base("base")
            except Child:
                return 2
            except Base:
                return 1
            return 0
        """)
        assert mod.foo(1) == 1   # Base caught by except Base
        assert mod.foo(0) == 2   # Child caught by except Child, not Base

    def test_except_as_custom(self):
        mod = self.compile("""
        class MyError(Exception):
            pass

        def foo() -> i32:
            try:
                raise MyError("hello")
            except MyError as e:
                if e == MyError("hello"):
                    return 1
            return 0
        """)
        assert mod.foo() == 1

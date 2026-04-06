from types import NoneType
from typing import TYPE_CHECKING, Optional

from spy import ast
from spy.backend.c import c_ast as C
from spy.backend.c.context import C_Ident, Context
from spy.fqn import FQN
from spy.location import Loc
from spy.textbuilder import TextBuilder
from spy.util import magic_dispatch, shortrepr
from spy.vm.b import TYPES
from spy.vm.function import W_ASTFunc, W_Func
from spy.vm.irtag import IRTag
from spy.vm.exc import W_Exception
from spy.vm.modules.posix import W__FILE
from spy.vm.modules.unsafe.ptr import W_Ptr

if TYPE_CHECKING:
    from spy.backend.c.cmodwriter import CModuleWriter


class CFuncWriter:
    ctx: Context
    cmodw: "CModuleWriter"
    tbc: TextBuilder
    fqn: FQN
    w_func: W_ASTFunc
    last_emitted_linenos: tuple[int, int]

    def __init__(
        self, ctx: Context, cmodw: "CModuleWriter", fqn: FQN, w_func: W_ASTFunc
    ) -> None:
        self.ctx = ctx
        self.cmodw = cmodw
        self.tbc = cmodw.tbc
        self.fqn = fqn
        self.w_func = w_func
        self.last_emitted_linenos = (-1, -1)  # see emit_lineno_maybe
        self._tmp_counter = 0          # for unique SPY_r0, SPY_r1, ... names
        self._try_counter = 0          # for unique SPY_EXC_0, SPY_END_0, ... labels
        self._try_exc_label_stack: list[str] = []   # innermost first
        self._try_finally_stack: list[list[ast.Stmt]] = []  # innermost first
        self._active_exc_var: list[str] = []        # for bare `raise`
        self._in_finally_emit: bool = False         # re-entry guard for emit_stmt_Return

    def ppc(self) -> None:
        """
        Pretty print the C code generated so far
        """
        print(self.tbc.build())

    def ppast(self) -> None:
        """
        Pretty print the AST
        """
        self.w_func.funcdef.pp()

    def emit(self) -> None:
        """
        Emit the code for the whole function
        """
        self.emit_lineno(self.w_func.funcdef.loc.line_start)
        c_func = self.ctx.c_function(self.fqn.c_name, self.w_func)
        self.tbc.wl(c_func.decl() + " {")
        with self.tbc.indent():
            self.emit_local_vars()
            for stmt in self.w_func.funcdef.body:
                self.emit_stmt(stmt)

            w_restype = self.w_func.w_functype.w_restype
            if w_restype is TYPES.w_NoneType:
                self.tbc.wl("return SPY_OK_void();")
            else:
                msg = "reached the end of the function without a `return`"
                self.tbc.wl(f"abort(); /* {msg} */")
        self.tbc.wl("}")

    def emit_local_vars(self) -> None:
        """
        Declare all local variables.

        We need to declare all of them in advance because C scoping rules are
        different than SPy scoping rules, so we emit the C declaration when we
        see e.g. a VarDef.
        """
        assert self.w_func.locals_types_w is not None
        param_names = [arg.name for arg in self.w_func.funcdef.args]
        for varname, w_T in self.w_func.locals_types_w.items():
            c_type = self.ctx.w2c(w_T)
            if (
                varname not in ("@return", "@if", "@and", "@or", "@while", "@assert")
                and varname not in param_names
            ):
                c_varname = C_Ident(varname)
                self.tbc.wl(f"{c_type} {c_varname};")
        # Local exception carrier for try/except/raise (Go-style).
        # Always emitted; the C compiler will optimize it away when unused.
        self.tbc.wl("spy_Exc *SPY_exc_local = NULL;")

    # ==============

    def emit_lineno_maybe(self, loc: Loc) -> None:
        """
        Emit a #line directive, but only if it's needed.
        """
        # line numbers corresponding to the last emitted #line
        last_spy, last_c = self.last_emitted_linenos
        #
        # line numbers as they are understood by the C compiler, i.e. what
        # goes to debuginfo if we don't emit a new #line
        cur_c = self.tbc.lineno
        cur_spy = last_spy + (cur_c - last_c) - 1
        #
        # desired spy line number, i.e. what we would like it to be
        desired_spy = loc.line_start
        if desired_spy != cur_spy:
            # time to emit a new #line directive
            self.emit_lineno(desired_spy)

    def emit_lineno(self, spyline: int) -> None:
        """
        Emit a #line directive, unconditionally
        """
        if self.cmodw.c_mod.spyfile is None:
            # we don't have an associated spyfile, so we cannot emit SPY_LINE
            return
        cline = self.tbc.lineno
        self.tbc.wl(f"#line SPY_LINE({spyline}, {cline})")
        self.last_emitted_linenos = (spyline, cline)

    def emit_stmt(self, stmt: ast.Stmt) -> None:
        self.emit_lineno_maybe(stmt.loc)
        magic_dispatch(self, "emit_stmt", stmt)

    def fmt_expr(self, expr: ast.Expr) -> C.Expr:
        # XXX: here we should probably handle typeconv, if present.
        # However, we cannot yet write a test for it because:
        #   - we cannot test DynamicCast because we don't support object
        #   - we cannot test NumericConv because the expressions are
        #     automatically converted by the C compiler anyway
        return magic_dispatch(self, "fmt_expr", expr)

    # ===== statements =====

    def emit_stmt_Pass(self, stmt: ast.Pass) -> None:
        pass

    def emit_stmt_Break(self, stmt: ast.Break) -> None:
        self.tbc.wl("break;")

    def emit_stmt_Continue(self, stmt: ast.Continue) -> None:
        self.tbc.wl("continue;")

    def emit_stmt_Return(self, ret: ast.Return) -> None:
        # Run any pending finally bodies before returning.  A guard flag prevents
        # a `return` inside a finally body from re-triggering the same blocks,
        # which would cause infinite recursion.
        if self._try_finally_stack and not self._in_finally_emit:
            self._in_finally_emit = True
            for finally_body in reversed(self._try_finally_stack):
                for stmt in finally_body:
                    self.emit_stmt(stmt)
            self._in_finally_emit = False
        v = self.fmt_expr(ret.value)
        w_restype = self.w_func.w_functype.w_restype
        suffix = self.ctx.result_suffix(w_restype)
        if v is C.Void():
            self.tbc.wl("return SPY_OK_void();")
        else:
            self.tbc.wl(f"return SPY_OK_{suffix}({v});")

    def emit_stmt_VarDef(self, vardef: ast.VarDef) -> None:
        # NOTE: the local variable declaration happens in emit_local_vars, here we just
        # assign the value
        if vardef.value:
            target = vardef.name.value
            v = self.fmt_expr(vardef.value)
            self.tbc.wl(f"{target} = {v};")

    def emit_stmt_Assign(self, assign: ast.Assign) -> None:
        assert False, "ast.Assign nodes should not survive redshifting"

    def emit_stmt_AssignLocal(self, assign: ast.AssignLocal) -> None:
        target = assign.target.value
        v = self.fmt_expr(assign.value)
        c_varname = C_Ident(target)
        self.tbc.wl(f"{c_varname} = {v};")

    def emit_stmt_AssignCell(self, assign: ast.AssignCell) -> None:
        v = self.fmt_expr(assign.value)
        target = assign.target_fqn.c_name
        c_varname = C_Ident(target)
        self.tbc.wl(f"{c_varname} = {v};")

    def emit_stmt_UnpackAssign(self, unpack: ast.UnpackAssign) -> None:
        if isinstance(unpack.value, ast.Tuple):
            # Blue tuple literal: directly assign each item to its target
            for target, item in zip(unpack.targets, unpack.value.items):
                c_target = C_Ident(target.value)
                v = self.fmt_expr(item)
                self.tbc.wl(f"{c_target} = {v};")
        else:
            # Red tuple (struct): we save the result into a tmp variable and the assign
            # all fields one by one. The code look like this more or less:
            # {
            #     T tmp = some_expression()
            #     a = tmp._item0;
            #     b = tmp._item1;
            # }
            assert unpack.value.w_T is not None
            c_tuple_type = self.ctx.w2c(unpack.value.w_T)
            v = self.fmt_expr(unpack.value)
            self.tbc.wl("{")
            with self.tbc.indent():
                self.tbc.wl(f"{c_tuple_type} tmp = {v};")
                for i, target in enumerate(unpack.targets):
                    c_target = C_Ident(target.value)
                    self.tbc.wl(f"{c_target} = tmp._item{i};")
            self.tbc.wl("}")

    def emit_stmt_StmtExpr(self, stmt: ast.StmtExpr) -> None:
        v = self.fmt_expr(stmt.value)
        if v is C.Void():
            pass
        else:
            self.tbc.wl(f"{v};")

    def emit_stmt_If(self, if_node: ast.If) -> None:
        test = self.fmt_expr(if_node.test)
        self.tbc.wl(f"if ({test})" + "{")
        with self.tbc.indent():
            for stmt in if_node.then_body:
                self.emit_stmt(stmt)
        #
        if if_node.else_body:
            self.tbc.wl("} else {")
            with self.tbc.indent():
                for stmt in if_node.else_body:
                    self.emit_stmt(stmt)
        #
        self.tbc.wl("}")

    def emit_stmt_While(self, while_node: ast.While) -> None:
        # Use `while (1) { if (!test) break; ... }` so that the condition is
        # re-evaluated on every iteration.  Emitting `while (fmt_expr(test))`
        # does NOT work when fmt_expr has side effects (e.g. SPy function calls
        # that emit result-variable + error-check code), because those side
        # effects would be emitted only once before the loop.
        self.tbc.wl("while (1) {")
        with self.tbc.indent():
            test = self.fmt_expr(while_node.test)
            self.tbc.wl(f"if (!({test})) break;")
            for stmt in while_node.body:
                self.emit_stmt(stmt)
        self.tbc.wl("}")

    def emit_stmt_Assert(self, assert_node: ast.Assert) -> None:
        test = self.fmt_expr(assert_node.test)
        self.tbc.wl(f"if (!({test}))" + " {")
        with self.tbc.indent():
            if assert_node.msg is not None:
                # TODO: assuming msg is always a string. extend the logic to work with other types
                msg = self.fmt_expr(assert_node.msg)
                self.tbc.wl(
                    f'spy_panic("AssertionError", ({msg})->utf8, '
                    f'"{assert_node.loc.filename}", {assert_node.loc.line_start});'
                )
            else:
                self.tbc.wl(
                    f'spy_panic("AssertionError", "assertion failed", '
                    f'"{assert_node.loc.filename}", {assert_node.loc.line_start});'
                )

        self.tbc.wl("}")

    def emit_stmt_Raise(self, raise_node: ast.Raise) -> None:
        # Non-bare raise was compiled by doppler to StmtExpr(Call(operator::raise, ...)).
        # Only bare `raise` reaches here.
        assert raise_node.exc is None, "non-bare raise should be a StmtExpr by now"
        assert self._active_exc_var, "bare `raise` outside an except handler"
        suffix = self.ctx.result_suffix(self.w_func.w_functype.w_restype)
        exc_var = self._active_exc_var[-1]
        if self._try_exc_label_stack:
            exc_label = self._try_exc_label_stack[-1]
            self.tbc.wl(f"SPY_exc_local = {exc_var};")
            self.tbc.wl(f"goto {exc_label};")
        else:
            self.tbc.wl(f"return SPY_ERR_{suffix}({exc_var});")

    def emit_stmt_Try(self, try_node: ast.Try) -> None:
        n = self._try_counter
        self._try_counter += 1
        exc_label = f"SPY_EXC_{n}"
        end_label = f"SPY_END_{n}"
        has_finally = bool(try_node.finalbody)
        has_orelse = bool(try_node.orelse)
        else_label = f"SPY_ELSE_{n}" if has_orelse else end_label
        suffix = self.ctx.result_suffix(self.w_func.w_functype.w_restype)

        # Push exception routing so calls inside the body jump here on error.
        self._try_exc_label_stack.append(exc_label)
        if has_finally:
            self._try_finally_stack.append(list(try_node.finalbody))

        for stmt in try_node.body:
            self.emit_stmt(stmt)

        if has_finally:
            self._try_finally_stack.pop()
        self._try_exc_label_stack.pop()

        # Normal path: run finally body then jump past the handlers.
        if has_finally:
            for stmt in try_node.finalbody:
                self.emit_stmt(stmt)
        self.tbc.wl(f"goto {else_label};")

        # Exception path.
        self.tbc.wl(f"{exc_label}:;")
        # Save the exception so bare `raise` inside a handler can use it even
        # after _emit_handler_prologue clears SPY_exc_local.
        saved_exc_var = f"SPY_handler_exc_{n}"
        self.tbc.wl(f"spy_Exc *{saved_exc_var} = SPY_exc_local;")
        self._active_exc_var.append(saved_exc_var)

        for handler in try_node.handlers:
            if not handler.exc_types:
                # bare `except:` — catch everything
                self.tbc.wl("{")
                with self.tbc.indent():
                    self._emit_handler_prologue(handler)
                    for stmt in handler.body:
                        self.emit_stmt(stmt)
                    if has_finally:
                        for stmt in try_node.finalbody:
                            self.emit_stmt(stmt)
                    self.tbc.wl(f"goto {end_label};")
                self.tbc.wl("}")
            else:
                fqn_types = handler.exc_types
                assert all(isinstance(t, ast.FQNConst) for t in fqn_types)
                conds = " || ".join(
                    f'spy_exc_matches(SPY_exc_local, "{t.fqn.symbol_name}")'
                    for t in [t for t in fqn_types if isinstance(t, ast.FQNConst)]
                )
                self.tbc.wl(f"if ({conds}) " + "{")
                with self.tbc.indent():
                    self._emit_handler_prologue(handler)
                    for stmt in handler.body:
                        self.emit_stmt(stmt)
                    if has_finally:
                        for stmt in try_node.finalbody:
                            self.emit_stmt(stmt)
                    self.tbc.wl(f"goto {end_label};")
                self.tbc.wl("}")

        self._active_exc_var.pop()

        # Unmatched: run finally, then propagate to outer handler or return.
        if has_finally:
            self.tbc.wl("{")
            with self.tbc.indent():
                # Save the active exception in case finalbody overwrites it.
                self.tbc.wl(f"spy_Exc *SPY_saved_exc_{n} = SPY_exc_local;")
                self.tbc.wl("SPY_exc_local = NULL;")
                for stmt in try_node.finalbody:
                    self.emit_stmt(stmt)
                # If finalbody didn't raise, restore the original exception.
                self.tbc.wl(
                    f"if (SPY_exc_local == NULL) "
                    f"SPY_exc_local = SPY_saved_exc_{n};"
                )
            self.tbc.wl("}")
        if self._try_exc_label_stack:
            outer_exc_label = self._try_exc_label_stack[-1]
            self.tbc.wl(f"goto {outer_exc_label};")
        else:
            self.tbc.wl(f"return SPY_ERR_{suffix}(SPY_exc_local);")

        # Else path (only if no exception was raised).
        if has_orelse:
            self.tbc.wl(f"{else_label}:;")
            for stmt in try_node.orelse:
                self.emit_stmt(stmt)

        self.tbc.wl(f"{end_label}:;")

    def _emit_handler_prologue(self, handler: ast.ExceptHandler) -> None:
        if handler.name is not None:
            c_varname = C_Ident(handler.name.value)
            # bind exception variable, clear the local carrier
            self.tbc.wl(f"{c_varname} = SPY_exc_local;")
            self.tbc.wl("SPY_exc_local = NULL;")
        else:
            self.tbc.wl("SPY_exc_local = NULL;")

    def _emit_propagate_err(
        self, err_expr: str, loc_src: Optional[str] = None
    ) -> None:
        suffix = self.ctx.result_suffix(self.w_func.w_functype.w_restype)
        fqn_str = str(self.fqn.human_name).replace('"', '\\"')
        if loc_src is not None:
            frame_add = (
                f'spy_exc_add_frame({err_expr}, "{fqn_str}", "{loc_src}"); '
            )
        else:
            frame_add = ''
        if self._try_exc_label_stack:
            exc_label = self._try_exc_label_stack[-1]
            self.tbc.wl(
                f"if ({err_expr}) "
                "{ "
                + frame_add
                + f"SPY_exc_local = {err_expr}; "
                f"goto {exc_label}; "
                "}"
            )
        else:
            if frame_add:
                self.tbc.wl(
                    f"if ({err_expr}) {{ {frame_add}"
                    f"return SPY_ERR_{suffix}({err_expr}); }}"
                )
            else:
                self.tbc.wl(
                    f"if ({err_expr}) return SPY_ERR_{suffix}({err_expr});"
                )

    def _build_exc_chain(self, etype: str) -> list[str]:
        from spy.vm.exc import exc_mro_names
        w_type = self.ctx.vm.lookup_exc_type(etype)
        if w_type is None:
            return [etype, "Exception"]
        return exc_mro_names(w_type)

    def _loc_to_c_str(self, loc: Loc) -> str:
        """Return a C-string-safe single-line representation of a source location."""
        try:
            src = loc.get_src().strip()
        except Exception:
            return ""
        # Collapse multiline source to its first line only.
        first_line = src.split('\n')[0].strip()
        return first_line.replace('\\', '\\\\').replace('"', '\\"')

    def _emit_exc_chain_static(self, chain: list[str]) -> str:
        # Deduplicate chains at the module level to avoid redefinitions when
        # multiple functions raise the same exception type.
        key = tuple(chain)
        if key in self.cmodw._exc_chain_cache:
            return self.cmodw._exc_chain_cache[key]
        n = len(self.cmodw._exc_chain_cache)
        entries = ", ".join(f'"{c}"' for c in chain) + ", NULL"
        chain_var = f"SPY_chain_{n}"
        self.cmodw.tbc_globals.wl(
            f"static const char * const {chain_var}[] = {{{entries}}};"
        )
        self.cmodw._exc_chain_cache[key] = chain_var
        return chain_var

    def _fmt_raise_call(self, call: ast.Call) -> C.Expr:
        # After doppler shifting, operator::raise is called with 4 StrConst/Constant
        # arguments: etype_name, message, filename, lineno.
        assert len(call.args) == 4
        etype_arg = call.args[0]
        msg_arg = call.args[1]
        assert isinstance(etype_arg, ast.StrConst)
        assert isinstance(msg_arg, ast.StrConst)
        etype = etype_arg.value
        chain = self._build_exc_chain(etype)
        chain_var = self._emit_exc_chain_static(chain)
        c_msg = self.fmt_expr(msg_arg)
        n = self._tmp_counter
        self._tmp_counter += 1
        self.tbc.wl(
            f"spy_Exc *SPY_exc_{n} = spy_exc_new({chain_var}, ({c_msg})->utf8);"
        )
        # Add the raise site as the first (innermost) frame entry.
        fqn_str = str(self.fqn.human_name).replace('"', '\\"')
        loc_src = self._loc_to_c_str(call.loc)
        self.tbc.wl(
            f'spy_exc_add_frame(SPY_exc_{n}, "{fqn_str}", "{loc_src}");'
        )
        suffix = self.ctx.result_suffix(self.w_func.w_functype.w_restype)
        if self._try_exc_label_stack:
            exc_label = self._try_exc_label_stack[-1]
            self.tbc.wl(f"SPY_exc_local = SPY_exc_{n};")
            self.tbc.wl(f"goto {exc_label};")
        else:
            self.tbc.wl(f"return SPY_ERR_{suffix}(SPY_exc_{n});")
        return C.Void()

    def _fmt_raise_red_call(self, call: ast.Call) -> C.Expr:
        # raise_red takes one runtime spy_Exc* argument and propagates it.
        assert len(call.args) == 1
        exc_expr = self.fmt_expr(call.args[0])
        suffix = self.ctx.result_suffix(self.w_func.w_functype.w_restype)
        fqn_str = str(self.fqn.human_name).replace('"', '\\"')
        loc_src = self._loc_to_c_str(call.loc)
        self.tbc.wl(f"spy_exc_add_frame({exc_expr}, \"{fqn_str}\", \"{loc_src}\");")
        if self._try_exc_label_stack:
            exc_label = self._try_exc_label_stack[-1]
            self.tbc.wl(f"SPY_exc_local = {exc_expr};")
            self.tbc.wl(f"goto {exc_label};")
        else:
            self.tbc.wl(f"return SPY_ERR_{suffix}({exc_expr});")
        return C.Void()

    def _is_exc_comparison(self, fqn: FQN, call: ast.Call) -> bool:
        """Return True if this call is a W_Exception __eq__ or __ne__."""
        from spy.vm.exc import W_Exception
        from spy.vm.object import W_Type
        if fqn.symbol_name not in ('eq', 'ne'):
            return False
        if not call.args:
            return False
        w_T = call.args[0].w_T
        return (
            isinstance(w_T, W_Type)
            and isinstance(w_T.pyclass, type)
            and issubclass(w_T.pyclass, W_Exception)
        )

    def _fmt_exc_comparison(self, fqn: FQN, call: ast.Call) -> C.Expr:
        """Emit a spy_exc_eq call for W_Exception __eq__/__ne__."""
        assert len(call.args) == 2
        a = self.fmt_expr(call.args[0])
        b = self.fmt_expr(call.args[1])
        result = C.Call("spy_exc_eq", [a, b])
        if fqn.symbol_name == 'ne':
            return C.UnaryOp("!", result)
        return result

    # ===== expressions =====

    def fmt_expr_Constant(self, const: ast.Constant) -> C.Expr:
        # unsupported literals are rejected directly by the parser, see
        # Parser.from_py_expr_Constant
        T = type(const.value)
        assert T in (int, float, complex, bool, str, NoneType)
        if T is NoneType:
            return C.Void()
        elif T in (int, float):
            return C.Literal(str(const.value))
        elif T is complex:
            val = complex(str(const.value))
            return C.Literal(
                "(spy_Complex128) {" + str(val.real) + ", " + str(val.imag) + "}"
            )
        elif T is bool:
            return C.Literal(str(const.value).lower())
        else:
            raise NotImplementedError("WIP")

    def fmt_expr_StrConst(self, const: ast.StrConst) -> C.Expr:
        # SPy string literals must be initialized as C globals. We want to
        # generate the following:
        #
        #     // global declarations
        #     static spy_Str SPY_g_str0 = {5, 0, "hello"};
        #     ...
        #     // literal expr
        #     &SPY_g_str0 /* "hello" */
        #
        # Note that in the literal expr we also put a comment showing what is
        # the content of the literal: hopefully this will make the code more
        # readable for humans.
        #
        # Emit the global decl
        s = const.value
        utf8 = s.encode("utf-8")
        v = self.cmodw.new_global_var("str")  # SPY_g_str0
        n = len(utf8)
        lit = C.Literal.from_bytes(utf8)
        init = "{%d, 0, %s}" % (n, lit)
        self.cmodw.tbc_globals.wl(f"static spy_Str {v} = {init};")
        #
        # shortstr is what we show in the comment, with a length limit
        comment = shortrepr(utf8.decode("utf-8"), 15)
        v = f"{v} /* {comment} */"
        return C.UnaryOp("&", C.Literal(v))

    def fmt_expr_FQNConst(self, const: ast.FQNConst) -> C.Expr:
        from spy.vm.exc import W_Exception
        w_obj = self.ctx.vm.lookup_global(const.fqn)
        if isinstance(w_obj, W_Ptr):
            # for each PtrType, we emit the corresponding NULL define with the
            # appropriate fqn name, see Context.new_ptr_type
            assert w_obj.addr == 0, "only NULL ptrs can be constants"
            return C.Literal(const.fqn.c_name)
        elif isinstance(w_obj, W_Func):
            return C.Literal(const.fqn.c_name)
        elif isinstance(w_obj, W__FILE):
            assert w_obj.h == 0, "only NULL _FILE can be a constant"
            return C.Literal("NULL")
        elif isinstance(w_obj, W_Exception):
            return self._fmt_exc_const(const.fqn, w_obj)
        else:
            assert False

    def _fmt_exc_const(self, fqn: FQN, w_exc: W_Exception) -> C.Expr:
        """Emit a static spy_Exc global for a prebuilt exception constant."""
        c_name = fqn.c_name
        if c_name not in self.cmodw._emitted_exc_consts:
            self.cmodw._emitted_exc_consts.add(c_name)
            # Emit the chain and the static exc struct.
            cls_name = type(w_exc).__name__
            etype = cls_name[2:] if cls_name.startswith("W_") else cls_name
            chain = self._build_exc_chain(etype)
            entries = ", ".join(f'"{c}"' for c in chain) + ", NULL"
            chain_var = f"{c_name}__chain"
            msg = w_exc.message.replace('\\', '\\\\').replace('"', '\\"')
            self.cmodw.tbc_globals.wl(
                f"static const char * const {chain_var}[] = {{{entries}}};"
            )
            self.cmodw.tbc_globals.wl(
                f"static spy_Exc {c_name} = {{{chain_var}, \"{msg}\", NULL}};"
            )
        return C.UnaryOp("&", C.Literal(c_name))

    def fmt_expr_Name(self, name: ast.Name) -> C.Expr:
        assert False, "ast.Name nodes should not survive redshifting"

    def fmt_expr_NameLocalDirect(self, name: ast.NameLocalDirect) -> C.Expr:
        varname = C_Ident(name.sym.name)
        return C.Literal(f"{varname}")

    def fmt_expr_NameOuterCell(self, name: ast.NameOuterCell) -> C.Expr:
        return C.Literal(name.fqn.c_name)

    def fmt_expr_NameOuterDirect(self, name: ast.NameOuterDirect) -> C.Expr:
        # at the moment of writing, closed-over variables are always blue, so
        # they should not survive redshifting
        assert False, "unexepcted NameOuterDirect"

    def fmt_expr_AssignExpr(self, assignexpr: ast.AssignExpr) -> C.Expr:
        return self._fmt_assignexpr(assignexpr.target.value, assignexpr.value)

    def fmt_expr_AssignExprLocal(self, assignexpr: ast.AssignExprLocal) -> C.Expr:
        return self._fmt_assignexpr(assignexpr.target.value, assignexpr.value)

    def fmt_expr_AssignExprCell(self, assignexpr: ast.AssignExprCell) -> C.Expr:
        return self._fmt_assignexpr(assignexpr.target_fqn.c_name, assignexpr.value)

    def _fmt_assignexpr(self, target: str, value_expr: ast.Expr) -> C.Expr:
        target_lit = C.Literal(target)
        value = self.fmt_expr(value_expr)
        return C.BinOp("=", target_lit, value)

    def fmt_expr_BinOp(self, binop: ast.BinOp) -> C.Expr:
        raise NotImplementedError(
            "ast.BinOp not supported. It should have been redshifted away"
        )

    def fmt_expr_And(self, op: ast.And) -> C.Expr:
        # Use a temp variable for proper short-circuit evaluation. When the left
        # side is a SPy function call, fmt_expr emits side-effecting code (the
        # result variable + error check), so we must not evaluate the right side
        # unconditionally.
        n = self._tmp_counter
        self._tmp_counter += 1
        l = self.fmt_expr(op.left)
        self.tbc.wl(f"bool SPY_and_{n} = {l};")
        self.tbc.wl(f"if (SPY_and_{n}) {{")
        with self.tbc.indent():
            r = self.fmt_expr(op.right)
            self.tbc.wl(f"SPY_and_{n} = {r};")
        self.tbc.wl("}")
        return C.Literal(f"SPY_and_{n}")

    def fmt_expr_Or(self, op: ast.Or) -> C.Expr:
        n = self._tmp_counter
        self._tmp_counter += 1
        l = self.fmt_expr(op.left)
        self.tbc.wl(f"bool SPY_or_{n} = {l};")
        self.tbc.wl(f"if (!SPY_or_{n}) {{")
        with self.tbc.indent():
            r = self.fmt_expr(op.right)
            self.tbc.wl(f"SPY_or_{n} = {r};")
        self.tbc.wl("}")
        return C.Literal(f"SPY_or_{n}")

    FQN2BinOp = {
        FQN("operator::i8_add"): "+",
        FQN("operator::i8_sub"): "-",
        FQN("operator::i8_mul"): "*",
        FQN("operator::i8_lshift"): "<<",
        FQN("operator::i8_rshift"): ">>",
        FQN("operator::i8_and"): "&",
        FQN("operator::i8_or"): "|",
        FQN("operator::i8_xor"): "^",
        FQN("operator::i8_eq"): "==",
        FQN("operator::i8_ne"): "!=",
        FQN("operator::i8_lt"): "<",
        FQN("operator::i8_le"): "<=",
        FQN("operator::i8_gt"): ">",
        FQN("operator::i8_ge"): ">=",
        #
        FQN("operator::u8_add"): "+",
        FQN("operator::u8_sub"): "-",
        FQN("operator::u8_mul"): "*",
        FQN("operator::u8_lshift"): "<<",
        FQN("operator::u8_rshift"): ">>",
        FQN("operator::u8_and"): "&",
        FQN("operator::u8_or"): "|",
        FQN("operator::u8_xor"): "^",
        FQN("operator::u8_eq"): "==",
        FQN("operator::u8_ne"): "!=",
        FQN("operator::u8_lt"): "<",
        FQN("operator::u8_le"): "<=",
        FQN("operator::u8_gt"): ">",
        FQN("operator::u8_ge"): ">=",
        #
        FQN("operator::i32_add"): "+",
        FQN("operator::i32_sub"): "-",
        FQN("operator::i32_mul"): "*",
        FQN("operator::i32_lshift"): "<<",
        FQN("operator::i32_rshift"): ">>",
        FQN("operator::i32_and"): "&",
        FQN("operator::i32_or"): "|",
        FQN("operator::i32_xor"): "^",
        FQN("operator::i32_eq"): "==",
        FQN("operator::i32_ne"): "!=",
        FQN("operator::i32_lt"): "<",
        FQN("operator::i32_le"): "<=",
        FQN("operator::i32_gt"): ">",
        FQN("operator::i32_ge"): ">=",
        #
        FQN("operator::u32_add"): "+",
        FQN("operator::u32_sub"): "-",
        FQN("operator::u32_mul"): "*",
        FQN("operator::u32_lshift"): "<<",
        FQN("operator::u32_rshift"): ">>",
        FQN("operator::u32_and"): "&",
        FQN("operator::u32_or"): "|",
        FQN("operator::u32_xor"): "^",
        FQN("operator::u32_eq"): "==",
        FQN("operator::u32_ne"): "!=",
        FQN("operator::u32_lt"): "<",
        FQN("operator::u32_le"): "<=",
        FQN("operator::u32_gt"): ">",
        FQN("operator::u32_ge"): ">=",
        #
        FQN("operator::f64_add"): "+",
        FQN("operator::f64_sub"): "-",
        FQN("operator::f64_mul"): "*",
        FQN("unsafe::f64_ieee754_div"): "/",
        FQN("operator::f64_eq"): "==",
        FQN("operator::f64_ne"): "!=",
        FQN("operator::f64_lt"): "<",
        FQN("operator::f64_le"): "<=",
        FQN("operator::f64_gt"): ">",
        FQN("operator::f64_ge"): ">=",
        # the following are NOT special cased, and are implemented in
        # operator.h. They are listed here to make emphasize that they are not
        # omitted from above by mistake:
        # T is any of the following types: i8, u8, i32, u32, f32 and f64
        # FQN('operator::T_div')
        # FQN('operator::T_floordiv')
        # FQN('operator::T_mod')
        # FQN('unsafe::T_unchecked_div')
        # FQN('unsafe::T_unchecked_floordiv')
        # FQN('unsafe::T_unchecked_mod')
    }

    FQN2UnaryOp = {
        FQN("operator::i8_neg"): "-",
        FQN("operator::i32_neg"): "-",
        FQN("operator::f64_neg"): "-",
    }

    def fmt_expr_Call(self, call: ast.Call) -> C.Expr:
        assert isinstance(call.func, ast.FQNConst), (
            "indirect calls are not supported yet"
        )
        fqn = call.func.fqn

        irtag = self.ctx.vm.get_irtag(fqn)
        if call.func.fqn.modname == "jsffi":
            self.cmodw.emit_jsffi_error_maybe()

        if op := self.FQN2BinOp.get(fqn):
            # binop special case
            assert len(call.args) == 2
            l, r = [self.fmt_expr(arg) for arg in call.args]
            return C.BinOp(op, l, r)

        elif op := self.FQN2UnaryOp.get(fqn):
            # unary op special case
            assert len(call.args) == 1
            v = self.fmt_expr(call.args[0])
            return C.UnaryOp(op, v)

        elif irtag.tag == "struct.make":
            return self.fmt_struct_make(fqn, call, irtag)

        elif irtag.tag == "struct.getfield":
            return self.fmt_struct_getfield(fqn, call, irtag)

        elif irtag.tag == "ptr.getfield":
            return self.fmt_ptr_getfield(fqn, call, irtag)

        elif irtag.tag == "ptr.setfield":
            return self.fmt_ptr_setfield(fqn, call)

        elif irtag.tag == "ptr.deref":
            # this is not strictly necessary as it's just a generic call, but
            # we handle ptr.deref explicitly for extra clarity
            return self.fmt_generic_call(fqn, call)

        elif irtag.tag in ("ptr.getitem", "ptr.store"):
            # see unsafe/ptr.py::w_GETITEM and w_SETITEM there, we insert an
            # extra "w_loc" argument, which is not needed by the C backend
            # because we rely on C's own mechanism to get line numbers.
            # Moreover, we don't have a way to render "W_Loc" consts to C.
            #
            # So, we just remove the last arguments. Note that this much match
            # with the signature of the load/store functions generated by
            # unsafe.h:SPY_PTR_FUNCTIONS.
            assert isinstance(call.args[-1], ast.LocConst)
            call.args.pop()  # remove it
            return self.fmt_generic_call(fqn, call)

        elif fqn == FQN("operator::raise"):
            return self._fmt_raise_call(call)

        elif fqn == FQN("operator::raise_red"):
            return self._fmt_raise_red_call(call)

        elif self._is_exc_comparison(fqn, call):
            return self._fmt_exc_comparison(fqn, call)

        else:
            return self.fmt_generic_call(fqn, call)

    def fmt_generic_call(self, fqn: FQN, call: ast.Call) -> C.Expr:
        self.ctx.add_include_maybe(fqn)
        c_name = fqn.c_name
        c_args = [self.fmt_expr(arg) for arg in call.args]

        # Only W_ASTFunc functions return spy_Result_T; C builtins return plain values.
        w_obj = self.ctx.vm.lookup_global(fqn)
        if not isinstance(w_obj, W_ASTFunc):
            return C.Call(c_name, c_args)

        # SPy function: returns spy_Result_T — unwrap and propagate on error.
        # Also record this call site as a traceback frame when propagating.
        w_restype = w_obj.w_functype.w_restype
        n = self._tmp_counter
        self._tmp_counter += 1
        call_str = C.Call(c_name, c_args)
        loc_src: Optional[str] = self._loc_to_c_str(call.loc) or None

        if w_restype is TYPES.w_NoneType:
            self.tbc.wl(f"spy_Result_void SPY_r{n} = {call_str};")
            self._emit_propagate_err(f"SPY_r{n}.err", loc_src=loc_src)
            return C.Void()
        else:
            c_result_type = self.ctx.w2c_result(w_restype)
            self.tbc.wl(f"{c_result_type} SPY_r{n} = {call_str};")
            self._emit_propagate_err(f"SPY_r{n}.err", loc_src=loc_src)
            return C.Literal(f"SPY_r{n}.value")

    def fmt_struct_make(self, fqn: FQN, call: ast.Call, irtag: IRTag) -> C.Expr:
        c_structtype = self.ctx.c_restype_by_fqn(fqn)
        c_args = [self.fmt_expr(arg) for arg in call.args]
        strargs = ", ".join(map(str, c_args))
        return C.Cast(c_structtype, C.Literal("{ %s }" % strargs))

    def fmt_struct_getfield(self, fqn: FQN, call: ast.Call, irtag: IRTag) -> C.Expr:
        assert len(call.args) == 1
        c_struct = self.fmt_expr(call.args[0])
        name = irtag.data["name"]
        return C.Dot(c_struct, name)

    def fmt_ptr_getfield(self, fqn: FQN, call: ast.Call, irtag: IRTag) -> C.Expr:
        assert isinstance(call.args[1], ast.StrConst)
        c_ptr = self.fmt_expr(call.args[0])
        attr = call.args[1].value
        offset = call.args[2]  # ignored
        c_field = C.PtrField(c_ptr, attr)
        if irtag.data["by"] == "byref":
            c_restype = self.ctx.c_restype_by_fqn(fqn)
            return C.PtrFieldByRef(c_restype, c_field)
        else:
            return c_field

    def fmt_ptr_setfield(self, fqn: FQN, call: ast.Call) -> C.Expr:
        assert isinstance(call.args[1], ast.StrConst)
        c_ptr = self.fmt_expr(call.args[0])
        attr = call.args[1].value
        offset = call.args[2]  # ignored
        c_lval = C.PtrField(c_ptr, attr)
        c_rval = self.fmt_expr(call.args[3])
        return C.BinOp("=", c_lval, c_rval)

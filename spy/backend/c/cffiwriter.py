import py.path

from spy.backend.c.context import Context
from spy.build.config import BuildConfig, CompilerConfig
from spy.fqn import FQN
from spy.textbuilder import TextBuilder
from spy.vm.function import W_ASTFunc


class CFFIWriter:
    """
    Generate a script *-cffi-build.py which contains all the necessary info
    to build cffi wrappers around a set of SPy modules.

    Imagine to compile `foo.spy`, which contains two functions `add` and
    `sub`. CFFIWriter produces more or less the following:

        ### foo.py
        import _foo
        add = _foo.lib.spy_foo_add
        sub = _foo.lib.spy_foo_sub

        ### _foo-cffi-build.py
        [...]
        ffibuilder.cdef('''
            int spy_foo_add(int x, int y);
            int spy_foo_sub(int x, int y);
        ''')
        src = '''
            #define spy_foo_add spy_foo$add
            #define spy_foo_sub spy_foo$sub
        '''

        ffibuilder.set_source(
            "_add",
            src,
            extra_sources=[...],        # list of all necessary .c files
            extra_compile_args=[...],   # cflags
            extra_link_args=[...],      # ldflags
            ...
        )

        if __name__ == '__main__':
            ffibuilder.compile()

    To generate `_add.so`, you can manually call `_add-cffi-build.py`, or
    integrate it inside a broader python packaging solution, e.g. by using the
    CFFI "Setuptools integration" as described here:
    https://cffi.readthedocs.io/en/latest/cdef.html
    """

    modname: str
    config: BuildConfig
    build_dir: py.path.local
    tb_py: TextBuilder
    tb_build: TextBuilder
    tb_cdef: TextBuilder
    tb_src: TextBuilder

    def __init__(
        self, modname: str, config: BuildConfig, build_dir: py.path.local
    ) -> None:
        self.modname = modname
        self.config = config
        self.build_dir = build_dir
        self.tb_py = TextBuilder()  # {modname}.py
        self.tb_build = TextBuilder()  # _{modname-cffi-build}.py
        self.init_py()
        self.init_cffi_build()

    def init_py(self) -> None:
        self.tb_py.wb(f"""
        import _{self.modname}
        """)

    def init_cffi_build(self) -> None:
        tb = self.tb_build
        tb.wb("""
        from cffi import FFI
        """)
        #
        tb.wl()
        tb.wl('CDEF = """')
        self.tb_cdef = tb.make_nested_builder()
        tb.wl('"""')
        tb.wl()
        tb.wl('SRC = """')
        self.tb_src = tb.make_nested_builder()
        tb.wl('"""')
        tb.wl()

    def finalize_cffi_build(self, cfiles: list[py.path.local]) -> None:
        srcdir = self.build_dir.join("src")
        comp = CompilerConfig(self.config)

        SOURCES = [str(f) for f in cfiles]
        CFLAGS = comp.cflags + [f"-I{srcdir}"]
        LDFLAGS = comp.ldflags

        self.tb_build.wb(f"""
        ffibuilder = FFI()
        ffibuilder.cdef(CDEF)
        ffibuilder.set_source(
            "_{self.modname}",
            SRC,
            sources={SOURCES},
            extra_compile_args={CFLAGS},
            extra_link_args={LDFLAGS},
        )

        if __name__ == "__main__":
            sofile = ffibuilder.compile(verbose=False)
            print(sofile)
        """)

    def write(self, cfiles: list[py.path.local]) -> py.path.local:
        assert self.config.kind == "py-cffi"
        self.finalize_cffi_build(cfiles)

        self.cffi_dir = self.build_dir.join("cffi")
        self.cffi_dir.ensure(dir=True)

        pyfile = self.cffi_dir.join(f"{self.modname}.py")
        pyfile.write(self.tb_py.build())

        build_script = self.cffi_dir.join(f"_{self.modname}-cffi-build.py")
        build_script.write(self.tb_build.build())
        return build_script

    def emit_include(self, header_name: str) -> None:
        self.tb_src.wb(f"""
        #include "{header_name}"
        """)

    def emit_func(self, ctx: Context, fqn: FQN, w_func: W_ASTFunc) -> None:
        """
        Emit CFFI declaration for the function.

        SPy functions return spy_Result_T internally (Go-style error handling),
        but CFFI callers expect plain C return types. We generate a thin C
        wrapper that unwraps the result and panics on error, then expose that
        wrapper through CFFI with a plain return-type declaration.
        """
        from spy.vm.b import TYPES
        from spy.backend.c.context import C_Function
        real_name = fqn.c_name
        cdef_name = real_name.replace("$", "_")
        w_restype = w_func.w_functype.w_restype

        # Build C_Function with plain (non-Result) return type for cdef.
        c_func_result = ctx.c_function(cdef_name, w_func)
        if w_restype is TYPES.w_NoneType:
            c_restype_plain = ctx.w2c(w_restype)  # "void"
        else:
            c_restype_plain = ctx.w2c(w_restype)
        c_func_plain = C_Function(cdef_name, c_func_result.params, c_restype_plain)
        self.tb_cdef.wl(c_func_plain.decl() + ";")

        # Generate a C wrapper that calls the real function and unwraps the result.
        param_names = ", ".join(str(p.name) for p in c_func_result.params
                                if p.c_type.name != "void")
        c_result_type = ctx.w2c_result(w_restype)
        suffix = ctx.result_suffix(w_restype)
        if w_restype is TYPES.w_NoneType:
            self.tb_src.wb(f"""
            void {cdef_name}({", ".join(f"{p.c_type} {p.name}" for p in c_func_result.params if p.c_type.name != "void") or "void"}) {{
                {c_result_type} spy_r_ = {real_name}({param_names});
                if (spy_r_.err) {{ spy_exc_print(spy_r_.err); abort(); }}
            }}
            """)
        else:
            self.tb_src.wb(f"""
            {c_restype_plain} {cdef_name}({", ".join(f"{p.c_type} {p.name}" for p in c_func_result.params if p.c_type.name != "void") or "void"}) {{
                {c_result_type} spy_r_ = {real_name}({param_names});
                if (spy_r_.err) {{ spy_exc_print(spy_r_.err); abort(); }}
                return spy_r_.value;
            }}
            """)
        #
        py_name = fqn.symbol_name
        self.tb_py.wl(f"{py_name} = _{self.modname}.lib.{cdef_name}")

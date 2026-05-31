#include "spy.h"
#include "spy/builtins.h"
#include <string.h>

/* Separated from exc.c so that exc.o does not pull in str.o transitively.
   str.o is large and references spy_panic (hence spy_debug_log in WASI mode),
   which would be imported even for programs that never trigger an exception. */

spy_StrObject *
spy_builtins$Exception$message(const spy_Exc *exc) {
    size_t length = strlen(exc->message);
    spy_StrObject *s = spy_str_alloc(length);
    memcpy((char *)spy_StrObject_UTF8(s), exc->message, length);
    return s;
}

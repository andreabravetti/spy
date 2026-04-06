#include "spy.h"
#include "spy/builtins.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

spy_Exc *
spy_exc_new(const char * const *etype_chain, const char *message) {
    spy_Exc *e = (spy_Exc *)malloc(sizeof(spy_Exc));
    e->etype_chain = etype_chain;
    // duplicate message so it outlives any temporary string
    e->message = strdup(message);
    e->frames = NULL;
    return e;
}

bool
spy_exc_matches(const spy_Exc *exc, const char *etype) {
    if (exc == NULL)
        return false;
    for (int i = 0; exc->etype_chain[i] != NULL; i++) {
        if (strcmp(exc->etype_chain[i], etype) == 0)
            return true;
    }
    return false;
}

bool
spy_exc_eq(const spy_Exc *a, const spy_Exc *b) {
    if (a == NULL || b == NULL)
        return a == b;
    return strcmp(a->message, b->message) == 0;
}

void
spy_exc_add_frame(spy_Exc *exc, const char *fqn, const char *loc_src) {
    spy_FrameEntry *entry = (spy_FrameEntry *)malloc(sizeof(spy_FrameEntry));
    entry->fqn = fqn;
    entry->loc_src = loc_src;
    entry->next = exc->frames;
    exc->frames = entry;
}

spy_Str *
spy_builtins$Exception$message(const spy_Exc *exc) {
    size_t length = strlen(exc->message);
    spy_Str *s = spy_str_alloc(length);
    memcpy((char *)s->utf8, exc->message, length);
    return s;
}

void
spy_exc_print(const spy_Exc *exc) {
    const char *etype = (exc->etype_chain && exc->etype_chain[0])
                        ? exc->etype_chain[0] : "Exception";
    fprintf(stderr, "%s: %s\n", etype, exc->message);
    for (spy_FrameEntry *f = exc->frames; f != NULL; f = f->next) {
        fprintf(stderr, "  at %s (%s)\n", f->fqn, f->loc_src);
    }
}

#ifndef SPY_EXC_H
#define SPY_EXC_H

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Singly-linked list of frame entries for traceback support.
typedef struct spy_FrameEntry {
    const char *fqn;
    const char *loc_src;
    struct spy_FrameEntry *next;
} spy_FrameEntry;

// spy_Exc is a heap-allocated exception object shared by all exception types.
// The type is identified by etype_chain, a NULL-terminated array of type names
// ordered from most specific to most general (the MRO).
typedef struct {
    const char * const *etype_chain;
    const char *message;
    spy_FrameEntry *frames;  // linked list head (outermost first after propagation)
} spy_Exc;

// Construct a new spy_Exc on the heap.
spy_Exc *spy_exc_new(const char * const *etype_chain, const char *message);

// Return true if exc matches the given type name (walks the MRO chain).
bool spy_exc_matches(const spy_Exc *exc, const char *etype);

// Compare two exceptions by message content.
bool spy_exc_eq(const spy_Exc *a, const spy_Exc *b);

// Prepend a frame entry to exc->frames (outermost caller adds last, so head is outermost).
void spy_exc_add_frame(spy_Exc *exc, const char *fqn, const char *loc_src);

// Print exception info to stderr (used by the C main() wrapper on uncaught exceptions).
void spy_exc_print(const spy_Exc *exc);

// -------------------------------------------------------------------------
// spy_Result_T: the Go-style (value, error) return type.
//
// Every generated SPy function returns one of these instead of a plain value.
// err == NULL means success; err != NULL means an exception was raised.
//
// Usage:
//   spy_Result_i32 r = some_spy_func(x);
//   if (r.err) return SPY_ERR_i32(r.err);   // propagate
//   int32_t val = r.value;                   // use value
//
// SPY_DEFINE_RESULT(C_type, suffix) generates the struct plus SPY_OK_/SPY_ERR_
// constructors for that type.
// -------------------------------------------------------------------------

#define SPY_DEFINE_RESULT(T, suffix)                        \
    typedef struct {                                        \
        T value;                                            \
        spy_Exc *err;                                       \
    } spy_Result_##suffix;                                  \
    static inline spy_Result_##suffix                       \
    SPY_OK_##suffix(T value) {                              \
        spy_Result_##suffix r;                              \
        r.value = value;                                    \
        r.err = NULL;                                       \
        return r;                                           \
    }                                                       \
    static inline spy_Result_##suffix                       \
    SPY_ERR_##suffix(spy_Exc *err) {                        \
        spy_Result_##suffix r;                              \
        memset(&r.value, 0, sizeof(r.value));               \
        r.err = err;                                        \
        return r;                                           \
    }

// For void-returning functions (NoneType).
typedef struct {
    spy_Exc *err;
} spy_Result_void;

static inline spy_Result_void SPY_OK_void(void) {
    spy_Result_void r;
    r.err = NULL;
    return r;
}

static inline spy_Result_void SPY_ERR_void(spy_Exc *err) {
    spy_Result_void r;
    r.err = err;
    return r;
}

// Standard instantiations for primitive types.
SPY_DEFINE_RESULT(int8_t,   i8)
SPY_DEFINE_RESULT(uint8_t,  u8)
SPY_DEFINE_RESULT(int32_t,  i32)
SPY_DEFINE_RESULT(uint32_t, u32)
SPY_DEFINE_RESULT(double,   f64)
SPY_DEFINE_RESULT(float,    f32)
SPY_DEFINE_RESULT(bool,     bool)

// spy_Result for pointer types (str, gc_ptr, etc.) is defined here generically
// since the value is just a pointer.
SPY_DEFINE_RESULT(void *,   ptr)

#endif /* SPY_EXC_H */

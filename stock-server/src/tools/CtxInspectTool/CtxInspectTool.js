// Reconstruction stub: this module is absent from the sourcemap snapshot.
// Generated as a reconstruction placeholder. Replace with a real
// implementation when the feature is needed.

const stub = new Proxy(function () {}, {
  get(_target, prop) {
    if (prop === Symbol.toPrimitive) return () => ''
    if (prop === 'then') return undefined
    if (prop === Symbol.iterator) return function* () {}
    if (prop === 'toString' || prop === 'valueOf') return () => ''
    return stub
  },
  apply() { return stub },
  construct() { return stub },
})

export const CtxInspectTool = stub
export default stub

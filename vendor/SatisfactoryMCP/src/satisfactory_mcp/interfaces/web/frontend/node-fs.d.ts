/* The one Node module `vite.config.ts` imports, declared by hand for the same reason that
 * file declares `process` itself rather than installing `@types/node`: the config runs
 * under Node and everything in `src/` runs in a browser, and pulling in the full Node types
 * to satisfy one import would put `require`, `Buffer` and `process` in scope for the whole
 * program, where every one of them is a mistake waiting to type-check.
 *
 * Declared here and not inline in `vite.config.ts` because a `declare module` in a file
 * with imports is a module AUGMENTATION, and TypeScript refuses to augment a module it
 * cannot resolve -- which is the very problem being solved. A `.d.ts` is an ambient
 * context, where the declaration simply defines the module.
 *
 * One function, in exactly the shape the config calls it: `URL` is fine as the path type
 * because `lib` includes DOM, and the return is `string` because an encoding is passed. */
declare module "node:fs" {
  export function readFileSync(path: URL | string, encoding: "utf-8"): string;
}

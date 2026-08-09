/// <reference types="node" />
/**
 * Vitest polyfills — must load BEFORE any test module that pulls in MSW.
 *
 * MSW v2's fetch interceptor eagerly imports `brotli-decompress`, which
 * calls `new TransformStream(...)` at module load time. jsdom@29 does not
 * expose the WHATWG Streams API, so MSW crashes at import time with
 * `ReferenceError: TransformStream is not defined`.
 *
 * Node 24+ ships the full Streams API at `node:stream/web`; we lift it
 * onto `globalThis` so MSW's import graph resolves cleanly. No-op when
 * the host already provides them (real browser, future jsdom).
 *
 * This file is loaded as the FIRST entry of `setupFiles` in
 * `vitest.config.ts` so its side effects run before `setup-tests.ts`
 * imports `@/__tests__/msw-handlers`.
 */
import { TransformStream, ReadableStream, WritableStream } from 'node:stream/web';

const g = globalThis as Record<string, unknown>;
if (!('TransformStream' in g)) g.TransformStream = TransformStream;
if (!('ReadableStream' in g)) g.ReadableStream = ReadableStream;
if (!('WritableStream' in g)) g.WritableStream = WritableStream;

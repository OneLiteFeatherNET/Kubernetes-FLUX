import { C as CompressOptions } from '../types-BTrX7__W.js';

interface OpenAILike {
    chat: {
        completions: {
            create: (params: any) => any;
        };
    };
    [key: string]: any;
}
/**
 * Wrap an OpenAI client to auto-compress messages before each request.
 *
 * Intercepts `client.chat.completions.create()` only. All other methods
 * (embeddings, images, audio, etc.) pass through unchanged.
 *
 * @example
 * ```typescript
 * import { withHeadroom } from 'headroom-ai/openai';
 * import OpenAI from 'openai';
 *
 * const client = withHeadroom(new OpenAI());
 * const response = await client.chat.completions.create({
 *   model: 'gpt-4o',
 *   messages: longConversation,
 * });
 * ```
 */
declare function withHeadroom<T extends OpenAILike>(client: T, options?: CompressOptions): T;

export { withHeadroom };

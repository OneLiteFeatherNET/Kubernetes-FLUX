import { C as CompressOptions } from '../types-BTrX7__W.js';

interface AnthropicLike {
    messages: {
        create: (params: any) => any;
    };
    [key: string]: any;
}
/**
 * Wrap an Anthropic client to auto-compress messages before each request.
 *
 * Intercepts `client.messages.create()` only. All other methods pass through.
 *
 * @example
 * ```typescript
 * import { withHeadroom } from 'headroom-ai/anthropic';
 * import Anthropic from '@anthropic-ai/sdk';
 *
 * const client = withHeadroom(new Anthropic());
 * const response = await client.messages.create({
 *   model: 'claude-sonnet-4-5-20250929',
 *   messages: longConversation,
 *   max_tokens: 1024,
 * });
 * ```
 */
declare function withHeadroom<T extends AnthropicLike>(client: T, options?: CompressOptions): T;

export { withHeadroom };

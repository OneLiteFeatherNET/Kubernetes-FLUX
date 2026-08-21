/**
 * Compression hooks matching Python headroom.hooks.
 * Allows customizing compression behavior with pre/post hooks.
 */
interface CompressContext {
    model: string;
    userQuery: string;
    turnNumber: number;
    toolCalls: string[];
    provider: string;
}
interface CompressEvent {
    tokensBefore: number;
    tokensAfter: number;
    tokensSaved: number;
    compressionRatio: number;
    transformsApplied: string[];
    ccrHashes: string[];
    model: string;
    userQuery: string;
    provider: string;
}
/**
 * Base class for compression hooks. Override methods to customize behavior.
 *
 * @example
 * ```typescript
 * class LoggingHooks extends CompressionHooks {
 *   postCompress(event: CompressEvent) {
 *     console.log(`Saved ${event.tokensSaved} tokens (${event.compressionRatio})`);
 *   }
 * }
 * ```
 */
declare class CompressionHooks {
    /**
     * Called before compression. Modify messages before they're sent to the proxy.
     */
    preCompress(messages: any[], _ctx: CompressContext): any[] | Promise<any[]>;
    /**
     * Compute per-message compression biases.
     * Return a map of message index -> bias (>1 = preserve more, <1 = compress more).
     */
    computeBiases(_messages: any[], _ctx: CompressContext): Record<number, number> | Promise<Record<number, number>>;
    /**
     * Called after compression. Observe-only — cannot modify the result.
     */
    postCompress(_event: CompressEvent): void | Promise<void>;
}
/**
 * Extract the last user message text from a messages array (any format).
 */
declare function extractUserQuery(messages: any[]): string;
/**
 * Count conversation turns (user+assistant pairs).
 */
declare function countTurns(messages: any[]): number;
/**
 * Extract tool call names from messages.
 */
declare function extractToolCalls(messages: any[]): string[];

/**
 * Core types for the Headroom TypeScript SDK.
 * Error classes moved to errors.ts — re-exported here for backwards compatibility.
 */

interface TextContentPart {
    type: "text";
    text: string;
}
interface ImageContentPart {
    type: "image_url";
    image_url: {
        url: string;
        detail?: "auto" | "low" | "high";
    };
}
type ContentPart = TextContentPart | ImageContentPart;
interface ToolCall {
    id: string;
    type: "function";
    function: {
        name: string;
        arguments: string;
    };
}
interface SystemMessage {
    role: "system";
    content: string;
}
interface UserMessage {
    role: "user";
    content: string | ContentPart[];
}
interface AssistantMessage {
    role: "assistant";
    content: string | null;
    tool_calls?: ToolCall[];
}
interface ToolMessage {
    role: "tool";
    content: string;
    tool_call_id: string;
}
type OpenAIMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage;
interface CompressOptions {
    model?: string;
    baseUrl?: string;
    apiKey?: string;
    timeout?: number;
    fallback?: boolean;
    retries?: number;
    client?: HeadroomClientInterface;
    /** Token budget — compress to fit within this limit. Used for compaction. */
    tokenBudget?: number;
    /** Compression hooks for pre/post processing. */
    hooks?: CompressionHooks;
    /** Integration slug sent as X-Headroom-Stack (e.g. "adapter_ts_openai"). */
    stack?: string;
}
interface CompressResult {
    /** Compressed messages in the same format as input. */
    messages: any[];
    tokensBefore: number;
    tokensAfter: number;
    tokensSaved: number;
    compressionRatio: number;
    transformsApplied: string[];
    ccrHashes: string[];
    compressed: boolean;
}
interface HeadroomClientOptions {
    baseUrl?: string;
    apiKey?: string;
    timeout?: number;
    fallback?: boolean;
    retries?: number;
    /** Integration slug sent as X-Headroom-Stack on every request. */
    stack?: string;
}
interface HeadroomClientInterface {
    compress(messages: OpenAIMessage[], options?: {
        model?: string;
        tokenBudget?: number;
    }): Promise<CompressResult>;
}

export { type AssistantMessage as A, type CompressOptions as C, type HeadroomClientOptions as H, type ImageContentPart as I, type OpenAIMessage as O, type SystemMessage as S, type TextContentPart as T, type UserMessage as U, type CompressResult as a, type HeadroomClientInterface as b, type CompressContext as c, type CompressEvent as d, CompressionHooks as e, type ContentPart as f, type ToolCall as g, type ToolMessage as h, countTurns as i, extractToolCalls as j, extractUserQuery as k };

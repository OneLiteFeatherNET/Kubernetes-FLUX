import { C as CompressOptions } from '../types-BTrX7__W.cjs';

/**
 * Gemini adapter — wraps Google Generative AI model with auto-compression.
 * Matches the pattern of openai.ts and anthropic.ts adapters.
 */

interface GeminiModelLike {
    generateContent: (params: any) => any;
    generateContentStream?: (params: any) => any;
    [key: string]: any;
}
/**
 * Wrap a Google Generative AI model to auto-compress before each request.
 *
 * Intercepts `model.generateContent()` and `model.generateContentStream()`.
 *
 * @example
 * ```typescript
 * import { withHeadroom } from 'headroom-ai/gemini';
 * import { GoogleGenerativeAI } from '@google/generative-ai';
 *
 * const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY!);
 * const model = withHeadroom(genAI.getGenerativeModel({ model: 'gemini-2.0-flash' }));
 *
 * const result = await model.generateContent({ contents: longConversation });
 * ```
 */
declare function withHeadroom<T extends GeminiModelLike>(model: T, options?: CompressOptions): T;

export { withHeadroom };

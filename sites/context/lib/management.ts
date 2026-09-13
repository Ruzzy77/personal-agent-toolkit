import { contextCall as call } from "./context";
export { ContextFailure } from "./context";
export const contextCall = (name: string, input: unknown) => call(name, input, "management");

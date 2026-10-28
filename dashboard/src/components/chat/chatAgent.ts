import { createContext } from 'react';

/** The agent whose chat this is: what a "Message from" line reads its message's body through. Null outside a chat. */
export const ChatAgentContext = createContext<string | null>(null);

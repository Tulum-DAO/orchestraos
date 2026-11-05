/**
 * menuAnswer.ts — the shape of one option of a kind='menu' card, as the API serves it. How a menu is answered (the
 * one question card's rules) lives in lib/multiPart.ts.
 */

export interface MenuOption {
  n: string;
  label: string;
  input_kind?: 'direct' | 'free_text' | 'chat' | string;
  detail?: string;
}

/**
 * `CommitOnBlur*` wrappers — text/textarea inputs that only commit to the
 * upstream edit store on blur (or Enter for single-line inputs).
 *
 * The legacy Svelte inspector + zustand's `applyEdit` together flooded the
 * undo stack with one entry per keystroke (T8 left the issue open). For
 * T8.5 we mirror the legacy UX by keeping a *local* string state for the
 * input value, and only invoking `onCommit` when the user blurs (or
 * presses Enter on the single-line variant) AND the value actually changed.
 *
 * Prop-resync: when the upstream `value` prop changes (undo, another tab,
 * agent edit), we resync local state via the "previous-prop in state"
 * pattern — comparing the prop against a tracked `prevValue` state during
 * render and updating both if it changed. This is React 19's
 * recommended replacement for `useEffect(() => setLocal(value), [value])`
 * (https://react.dev/learn/you-might-not-need-an-effect#resetting-all-state-when-a-prop-changes)
 * and avoids the `react-hooks/set-state-in-effect` cascading-render warning.
 *
 * Selects, switches, and other one-shot inputs commit immediately (a
 * single click can't realistically flood the stack).
 */
import { useState } from 'react';
import type {
  InputHTMLAttributes,
  KeyboardEvent,
  TextareaHTMLAttributes,
} from 'react';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

type InputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'value' | 'onChange' | 'onBlur'
>;

export interface CommitOnBlurInputProps extends InputProps {
  value: string;
  onCommit: (next: string) => void;
}

export function CommitOnBlurInput({
  value,
  onCommit,
  onKeyDown,
  ...rest
}: CommitOnBlurInputProps) {
  const [local, setLocal] = useState(value);
  // Prev-prop-in-state: resync local buffer when upstream `value` changes.
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setLocal(value);
  }

  const commit = () => {
    if (local !== value) onCommit(local);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // Ignore the Enter that CONFIRMS an IME composition (Chinese/Japanese/…
    // input methods): it must NOT blur/commit, or the composed text gets
    // committed AND then re-inserted by the IME on compositionend → the
    // characters appear twice. `isComposing` / keyCode 229 = mid-composition.
    if (
      e.key === 'Enter' &&
      !e.shiftKey &&
      !(e.nativeEvent.isComposing || e.keyCode === 229)
    ) {
      (e.currentTarget as HTMLInputElement).blur();
    }
    onKeyDown?.(e);
  };

  return (
    <Input
      {...rest}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={handleKeyDown}
    />
  );
}

type NumberInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'value' | 'onChange' | 'onBlur' | 'type'
>;

export interface CommitOnBlurNumberProps extends NumberInputProps {
  value: number;
  onCommit: (next: number) => void;
  /**
   * `'int'` uses `parseInt(_, 10)`, `'float'` uses `parseFloat`. Default
   * `'float'`. Step / min / max props still flow to the underlying input
   * so the spinner UX matches.
   */
  kind?: 'int' | 'float';
}

/**
 * Numeric variant of `CommitOnBlurInput`. Keeps a local string buffer
 * while the user types, only commits a parsed number on blur (or
 * Enter). Same prop-resync pattern as the text variants — when the
 * upstream `value` changes, the buffer follows.
 *
 * Invalid / empty commits revert the local buffer to the current
 * `value` rather than calling `onCommit` with NaN. This matches the
 * fall-back-to-current semantics of the original per-keystroke
 * `parseNumberOrUndefined` ?? current pattern, but consolidates the
 * undo-stack push to one entry per committed value instead of one per
 * keystroke.
 */
export function CommitOnBlurNumber({
  value,
  onCommit,
  onKeyDown,
  kind = 'float',
  ...rest
}: CommitOnBlurNumberProps) {
  const initialString = Number.isFinite(value) ? String(value) : '';
  const [local, setLocal] = useState(initialString);
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setLocal(Number.isFinite(value) ? String(value) : '');
  }

  const commit = () => {
    const trimmed = local.trim();
    const parsed = kind === 'int' ? parseInt(trimmed, 10) : parseFloat(trimmed);
    if (Number.isFinite(parsed) && parsed !== value) {
      onCommit(parsed);
    } else if (!Number.isFinite(parsed)) {
      // Invalid / empty — silently revert the buffer rather than emit
      // NaN. This also covers the "user cleared the field" case.
      setLocal(Number.isFinite(value) ? String(value) : '');
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // Ignore the Enter that CONFIRMS an IME composition (Chinese/Japanese/…
    // input methods): it must NOT blur/commit, or the composed text gets
    // committed AND then re-inserted by the IME on compositionend → the
    // characters appear twice. `isComposing` / keyCode 229 = mid-composition.
    if (
      e.key === 'Enter' &&
      !e.shiftKey &&
      !(e.nativeEvent.isComposing || e.keyCode === 229)
    ) {
      (e.currentTarget as HTMLInputElement).blur();
    }
    onKeyDown?.(e);
  };

  return (
    <Input
      {...rest}
      type="number"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={handleKeyDown}
    />
  );
}

type TextareaProps = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  'value' | 'onChange' | 'onBlur'
>;

export interface CommitOnBlurTextareaProps extends TextareaProps {
  value: string;
  onCommit: (next: string) => void;
}

export function CommitOnBlurTextarea({
  value,
  onCommit,
  ...rest
}: CommitOnBlurTextareaProps) {
  const [local, setLocal] = useState(value);
  // Prev-prop-in-state: resync local buffer when upstream `value` changes.
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setLocal(value);
  }

  const commit = () => {
    if (local !== value) onCommit(local);
  };

  return (
    <Textarea
      {...rest}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
    />
  );
}

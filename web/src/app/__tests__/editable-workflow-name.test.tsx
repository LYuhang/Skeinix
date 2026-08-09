import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NameField } from '@/app/EditableWorkflowName';

describe('NameField', () => {
  it('shows the name; click → edit; Enter → onRename(new)', async () => {
    const onRename = vi.fn();
    render(<NameField name="Old Name" readOnly={false} onRename={onRename} />);
    await userEvent.click(screen.getByText('Old Name'));
    const input = screen.getByRole('textbox');
    await userEvent.clear(input);
    await userEvent.type(input, 'New Name{Enter}');
    expect(onRename).toHaveBeenCalledWith('New Name');
  });

  it('does not call onRename when unchanged', async () => {
    const onRename = vi.fn();
    render(<NameField name="Same" readOnly={false} onRename={onRename} />);
    await userEvent.click(screen.getByText('Same'));
    await userEvent.type(screen.getByRole('textbox'), '{Enter}');
    expect(onRename).not.toHaveBeenCalled();
  });

  it('readOnly: click does NOT enter edit mode', async () => {
    const onRename = vi.fn();
    render(<NameField name="Fixed" readOnly onRename={onRename} />);
    await userEvent.click(screen.getByText('Fixed'));
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('shows a pencil button; clicking it enters edit mode', async () => {
    render(<NameField name="Old" readOnly={false} onRename={vi.fn()} />);
    const pencil = screen.getByTestId('rename-workflow');
    expect(pencil).toBeInTheDocument();
    await userEvent.click(pencil);
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('readOnly: no pencil button', () => {
    render(<NameField name="Fixed" readOnly onRename={vi.fn()} />);
    expect(screen.queryByTestId('rename-workflow')).toBeNull();
  });
});

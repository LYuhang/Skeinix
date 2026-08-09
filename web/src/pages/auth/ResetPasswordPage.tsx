/**
 * `/reset-password` — two-step password recovery.
 *
 * Mode is driven by the URL search params, mirroring how reset-link
 * emails work: the recovery email contains a link like
 * `/reset-password?step=confirm&token=<reset_token>`. With no params we
 * render the email-entry form (step 1); with `step=confirm` we render
 * the new-password form (step 2) pre-filled with the URL's token.
 *
 * Step 1 always shows a generic "if this email is registered, we sent
 * one" message — no email enumeration. Step 2 surfaces the backend's
 * invalid-or-expired-token detail on 400 verbatim.
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { AuthLayout } from '@/pages/auth/AuthLayout';
import {
  AuthApiError,
  confirmPasswordReset,
  requestPasswordReset,
} from '@/stores/auth';

const requestSchema = z.object({
  email: z.string().email(),
});
type RequestValues = z.infer<typeof requestSchema>;

const confirmSchema = z.object({
  reset_token: z.string().min(1),
  new_password: z.string().min(8),
});
type ConfirmValues = z.infer<typeof confirmSchema>;

export function ResetPasswordPage() {
  const [search] = useSearchParams();
  const step = search.get('step') === 'confirm' ? 'confirm' : 'request';
  return step === 'confirm' ? <ConfirmStep /> : <RequestStep />;
}

function RequestStep() {
  const { t } = useTranslation();
  const [sent, setSent] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const form = useForm<RequestValues>({
    resolver: zodResolver(requestSchema),
    defaultValues: { email: '' },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      await requestPasswordReset(values.email);
      setSent(true);
    } catch (err) {
      if (err instanceof AuthApiError) {
        if (err.status === 422) {
          setSubmitError(t('auth_reset_request_invalid_email',
            'Please enter a valid email address.'));
        } else {
          setSubmitError(err.detail);
        }
      } else setSubmitError(t('auth_error_network', 'Network error. Please retry.'));
    }
  });

  return (
    <AuthLayout
      title={t('auth_reset_request_title', 'Reset your password')}
      subtitle={t(
        'auth_reset_request_subtitle',
        'We will email you a reset token if your account exists.',
      )}
    >
      {sent ? (
        <div className="flex flex-col gap-4">
          <p
            role="status"
            className="rounded-md border bg-muted/40 px-3 py-2 text-sm"
          >
            {t(
              'auth_reset_request_sent',
              'If that email is registered, a reset message has been sent.',
            )}
          </p>
          <Link
            to="/login"
            className="text-sm font-medium underline-offset-2 hover:underline"
          >
            {t('auth_back_to_login', '← Back to sign in')}
          </Link>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="reset-email">
              {t('auth_email_label', 'Email')}
            </Label>
            <Input
              id="reset-email"
              type="email"
              autoComplete="email"
              autoFocus
              placeholder={t('auth_email_placeholder', 'you@example.com')}
              aria-invalid={form.formState.errors.email ? true : undefined}
              {...form.register('email')}
            />
            {form.formState.errors.email ? (
              <p className="text-xs text-destructive">
                {t('auth_email_invalid', 'Please enter a valid email.')}
              </p>
            ) : null}
          </div>
          {submitError ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
            >
              {submitError}
            </p>
          ) : null}
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting
              ? t('auth_sending', 'Sending…')
              : t('auth_reset_request_submit', 'Send reset email')}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            <Link
              to="/login"
              className="font-medium text-foreground underline-offset-2 hover:underline"
            >
              {t('auth_back_to_login', '← Back to sign in')}
            </Link>
          </p>
        </form>
      )}
    </AuthLayout>
  );
}

function ConfirmStep() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [submitError, setSubmitError] = useState<string | null>(null);

  const form = useForm<ConfirmValues>({
    resolver: zodResolver(confirmSchema),
    defaultValues: {
      reset_token: search.get('token') ?? '',
      new_password: '',
    },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      await confirmPasswordReset(values.reset_token, values.new_password);
      toast.success(
        t('auth_reset_confirm_success', 'Password updated. Please sign in.'),
      );
      navigate('/login', { replace: true });
    } catch (err) {
      if (err instanceof AuthApiError) {
        // 422 carries an array `detail` from FastAPI's validator; authPost
        // falls back to "HTTP 422" in that case, so map it to a user-facing
        // generic-validation message rather than rendering the raw fallback.
        if (err.status === 422) {
          setSubmitError(t('auth_reset_confirm_invalid_input',
            'Reset token or new password is invalid.'));
        } else {
          setSubmitError(err.detail);
        }
      } else setSubmitError(t('auth_error_network', 'Network error. Please retry.'));
    }
  });

  return (
    <AuthLayout
      title={t('auth_reset_confirm_title', 'Set a new password')}
      subtitle={t(
        'auth_reset_confirm_subtitle',
        'Paste the reset token from the email and choose a new password.',
      )}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="reset-token">
            {t('auth_reset_token_label', 'Reset token')}
          </Label>
          <Input
            id="reset-token"
            type="text"
            autoComplete="one-time-code"
            aria-invalid={form.formState.errors.reset_token ? true : undefined}
            {...form.register('reset_token')}
          />
          {form.formState.errors.reset_token ? (
            <p className="text-xs text-destructive">
              {t('auth_reset_token_required', 'Reset token is required.')}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="reset-new-password">
            {t('auth_new_password_label', 'New password')}
          </Label>
          <Input
            id="reset-new-password"
            type="password"
            autoComplete="new-password"
            placeholder={t('auth_password_min_hint', 'At least 8 characters')}
            aria-invalid={form.formState.errors.new_password ? true : undefined}
            {...form.register('new_password')}
          />
          {form.formState.errors.new_password ? (
            <p className="text-xs text-destructive">
              {t('auth_password_too_short', 'Password must be at least 8 characters.')}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t('auth_password_min_hint', 'At least 8 characters')}
            </p>
          )}
        </div>
        {submitError ? (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          >
            {submitError}
          </p>
        ) : null}
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting
            ? t('auth_updating', 'Updating…')
            : t('auth_reset_confirm_submit', 'Update password')}
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          <Link
            to="/login"
            className="font-medium text-foreground underline-offset-2 hover:underline"
          >
            {t('auth_back_to_login', '← Back to sign in')}
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}

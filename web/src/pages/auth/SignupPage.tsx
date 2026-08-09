/**
 * `/signup` — new-user registration.
 *
 * The API auto-logs the new user in (returns a session_token alongside
 * the user record), so on success we navigate straight to `/chat`.
 * A client-side "confirm password" field guards against typos; it is
 * never sent to the backend.
 *
 * On `409`, surface the backend's already-registered detail. On `422`, we
 * fall back to a generic "check your email and password" — the only 422
 * branch on this endpoint is "password too short" or "bad email", both
 * of which the client-side zod schema also catches, so a 422 here means
 * the server caught something the client missed (unlikely; defensive).
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Link, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { AuthLayout } from '@/pages/auth/AuthLayout';
import { AuthApiError, useAuthStore } from '@/stores/auth';

// The two-field "passwords must match" check uses zod's superRefine so the
// error attaches to `confirm` (not `password`), keeping the inline help
// next to the field the user has to fix.
const schema = z
  .object({
    username: z.string().trim().min(1).max(80),
    email: z.string().email(),
    password: z.string().min(8),
    confirm: z.string(),
  })
  .superRefine((val, ctx) => {
    if (val.password !== val.confirm) {
      ctx.addIssue({
        code: 'custom',
        path: ['confirm'],
        message: 'auth_password_mismatch',
      });
    }
  });

type FormValues = z.infer<typeof schema>;

export function SignupPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const signup = useAuthStore((s) => s.signup);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', email: '', password: '', confirm: '' },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      await signup(values.email, values.password, values.username.trim());
      navigate('/chat', { replace: true });
    } catch (err) {
      if (err instanceof AuthApiError) {
        if (err.status === 409) {
          setSubmitError(err.detail);
        } else if (err.status === 422) {
          setSubmitError(
            t(
              'auth_signup_invalid',
              'Please check that your email is valid and your password is at least 8 characters.',
            ),
          );
        } else {
          setSubmitError(err.detail);
        }
      } else {
        setSubmitError(t('auth_error_network', 'Network error. Please retry.'));
      }
    }
  });

  const pwErr = form.formState.errors.password;
  const confirmErr = form.formState.errors.confirm;
  const emailErr = form.formState.errors.email;
  const usernameErr = form.formState.errors.username;

  return (
    <AuthLayout
      title={t('auth_signup_title', 'Create an account')}
      subtitle={t('auth_signup_subtitle', 'Sign up to start building workflows.')}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="signup-username">
            {t('auth_username_label', 'Username')}
          </Label>
          <Input
            id="signup-username"
            type="text"
            autoComplete="name"
            autoFocus
            placeholder={t('auth_username_placeholder', 'Your name')}
            aria-invalid={usernameErr ? true : undefined}
            {...form.register('username')}
          />
          {usernameErr ? (
            <p className="text-xs text-destructive">
              {t('auth_username_invalid', 'Please enter a username.')}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="signup-email">
            {t('auth_email_label', 'Email')}
          </Label>
          <Input
            id="signup-email"
            type="email"
            autoComplete="email"
            placeholder={t('auth_email_placeholder', 'you@example.com')}
            aria-invalid={emailErr ? true : undefined}
            {...form.register('email')}
          />
          {emailErr ? (
            <p className="text-xs text-destructive">
              {t('auth_email_invalid', 'Please enter a valid email.')}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="signup-password">
            {t('auth_password_label', 'Password')}
          </Label>
          <div className="relative">
            <Input
              id="signup-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              placeholder={t('auth_password_min_hint', 'At least 8 characters')}
              aria-invalid={pwErr ? true : undefined}
              className="pr-10"
              {...form.register('password')}
            />
            <button
              type="button"
              className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
              aria-label={
                showPassword
                  ? t('auth_password_hide', 'Hide password')
                  : t('auth_password_show', 'Show password')
              }
              title={
                showPassword
                  ? t('auth_password_hide', 'Hide password')
                  : t('auth_password_show', 'Show password')
              }
              onClick={() => setShowPassword((v) => !v)}
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
          {pwErr ? (
            <p className="text-xs text-destructive">
              {t('auth_password_too_short', 'Password must be at least 8 characters.')}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t('auth_password_min_hint', 'At least 8 characters')}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="signup-confirm">
            {t('auth_confirm_password_label', 'Confirm password')}
          </Label>
          <div className="relative">
            <Input
              id="signup-confirm"
              type={showConfirm ? 'text' : 'password'}
              autoComplete="new-password"
              aria-invalid={confirmErr ? true : undefined}
              className="pr-10"
              {...form.register('confirm')}
            />
            <button
              type="button"
              className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
              aria-label={
                showConfirm
                  ? t('auth_password_hide', 'Hide password')
                  : t('auth_password_show', 'Show password')
              }
              title={
                showConfirm
                  ? t('auth_password_hide', 'Hide password')
                  : t('auth_password_show', 'Show password')
              }
              onClick={() => setShowConfirm((v) => !v)}
            >
              {showConfirm ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
          {confirmErr ? (
            <p className="text-xs text-destructive">
              {t('auth_password_mismatch', 'Passwords do not match.')}
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
            ? t('auth_creating_account', 'Creating account…')
            : t('auth_signup_submit', 'Create account')}
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          {t('auth_have_account', 'Already have an account?')}{' '}
          <Link
            to="/login"
            className="font-medium text-foreground underline-offset-2 hover:underline"
          >
            {t('auth_login_link', 'Sign in')}
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}

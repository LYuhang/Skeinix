/**
 * `EmbedLogin` — the app's real login UI, rendered INSIDE the side-panel embed
 * used when the extension does not yet have an authenticated session.
 *
 * Chrome partitions the embed's storage from the user's normal web session,
 * so a cold side panel is unauthenticated and the user must log in once
 * here. Rather than link out to `/login` (a dead end inside the iframe), we
 * render the SAME `AuthLayout` + email/password form + zod schema + auth-store
 * `login()` as `LoginPage` — the only difference is that on success we do not
 * navigate the iframe into a full app route. Instead the auth store's `token`
 * flips to non-null and
 * `EmbedChatPage` re-renders with the resolved carrier scope. The session persists in
 * the embed's partitioned localStorage, so the next open is remembered.
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { AuthLayout } from '@/pages/auth/AuthLayout';
import { LoginMfaForm } from '@/components/auth/LoginMfaForm';
import {
  AuthApiError,
  type LoginMfaRequired,
  useAuthStore,
} from '@/stores/auth';

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

type FormValues = z.infer<typeof schema>;

export function EmbedLogin() {
  const { t } = useTranslation();
  const login = useAuthStore((s) => s.login);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pendingMfa, setPendingMfa] = useState<LoginMfaRequired | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      // On success the auth store sets `token`; EmbedChatPage advances to the
      // embedded chat. No navigation inside the embed (would mount the full
      // app shell in the iframe).
      const pending = await login(values.email, values.password);
      if (pending) setPendingMfa(pending);
    } catch (err) {
      if (err instanceof AuthApiError) {
        setSubmitError(err.detail);
      } else {
        setSubmitError(t('auth_error_network', 'Network error. Please retry.'));
      }
    }
  });

  return (
    <AuthLayout
      title={t('auth_login_title', 'Sign in')}
      subtitle={t('auth_login_subtitle', 'Sign in to your workspace.')}
    >
      {pendingMfa ? (
        <LoginMfaForm
          pending={pendingMfa}
          onAuthenticated={() => setPendingMfa(null)}
          onBack={() => {
            setPendingMfa(null);
            form.setValue('password', '');
          }}
        />
      ) : (
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="embed-login-email">
            {t('auth_email_label', 'Email')}
          </Label>
          <Input
            id="embed-login-email"
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
        <div className="flex flex-col gap-2">
          <Label htmlFor="embed-login-password">
            {t('auth_password_label', 'Password')}
          </Label>
          <Input
            id="embed-login-password"
            type="password"
            autoComplete="current-password"
            placeholder={t('auth_password_placeholder', '••••••••')}
            aria-invalid={form.formState.errors.password ? true : undefined}
            {...form.register('password')}
          />
          {form.formState.errors.password ? (
            <p className="text-xs text-destructive">
              {t('auth_password_required', 'Password is required.')}
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
            ? t('auth_signing_in', 'Signing in…')
            : t('auth_login_submit', 'Sign in')}
        </Button>
      </form>
      )}
    </AuthLayout>
  );
}

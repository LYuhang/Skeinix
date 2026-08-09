/**
 * `/login` — email + password sign-in.
 *
 * Matches the shadcn + react-hook-form + zod pattern established by
 * `CreateWorkflowDialog`. On success we navigate to `/chat`. On
 * For `401`, surface the backend's generic invalid-credential detail — we
 * deliberately do NOT distinguish unknown-email from wrong-password, to
 * avoid email enumeration. `429` is its own message ("rate-limit").
 */
import { lazy, Suspense, useEffect, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Link, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { AuthLayout } from '@/pages/auth/AuthLayout';
import { LoginMfaForm } from '@/components/auth/LoginMfaForm';
import {
  AuthApiError,
  type LoginMfaRequired,
  useAuthStore,
} from '@/stores/auth';
import { getApiBase } from '@/lib/base-path';

const schema = z.object({
  email: z.string().trim().min(1).refine((value) => {
    if (value === 'test') return true;
    return z.string().email().safeParse(value).success;
  }, 'email'),
  password: z.string().min(1),
});

type FormValues = z.infer<typeof schema>;
const API_BASE = getApiBase();
const EnterpriseSsoLogin = lazy(() => import('@/components/auth/EnterpriseSsoLogin')
  .then((module) => ({ default: module.EnterpriseSsoLogin })));

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [enableTestUser, setEnableTestUser] = useState(false);
  const [enterpriseSsoEnabled, setEnterpriseSsoEnabled] = useState(false);
  const [loginMethod, setLoginMethod] = useState<'email' | 'sso'>('email');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [pendingMfa, setPendingMfa] = useState<LoginMfaRequired | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });
  const [watchedEmail, watchedPassword] = useWatch({
    control: form.control,
    name: ['email', 'password'],
  });
  const showTestWarning = watchedEmail === 'test' && watchedPassword === 'test';

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/v1/public-config`)
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        if (cancelled) return;
        const enabled = Boolean(payload?.enable_test_user);
        setEnableTestUser(enabled);
        const ssoEnabled = Boolean(payload?.enterprise_sso_enabled);
        setEnterpriseSsoEnabled(ssoEnabled);
        if (!ssoEnabled) setLoginMethod('email');
        if (enabled) {
          const current = form.getValues();
          if (!current.email && !current.password) {
            form.setValue('email', 'test', { shouldDirty: false });
            form.setValue('password', 'test', { shouldDirty: false });
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEnableTestUser(false);
          setEnterpriseSsoEnabled(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [form]);

  const onSubmit = form.handleSubmit(async (values) => {
    setSubmitError(null);
    try {
      const pending = await login(values.email, values.password);
      if (pending) {
        setPendingMfa(pending);
      } else {
        navigate('/chat', { replace: true });
      }
    } catch (err) {
      if (err instanceof AuthApiError) {
        // Backend `detail` strings are already the localized Chinese
        // messages that the spec requires byte-for-byte; render them
        // verbatim. (English UI users will see the same Chinese detail —
        // this is intentional alignment with the API contract.)
        setSubmitError(err.detail);
      } else {
        setSubmitError(t('auth_error_network', 'Network error. Please retry.'));
      }
    }
  });

  const emailLogin = (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Label htmlFor="login-email">
          {t('auth_email_label', 'Email')}
        </Label>
        <Input
          id="login-email"
          type="text"
          inputMode="email"
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
        <div className="flex items-center justify-between">
          <Label htmlFor="login-password">
            {t('auth_password_label', 'Password')}
          </Label>
          <Link
            to="/reset-password"
            className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            {t('auth_forgot_password', 'Forgot password?')}
          </Link>
        </div>
        <div className="relative">
          <Input
            id="login-password"
            type={passwordVisible ? 'text' : 'password'}
            autoComplete="current-password"
            placeholder={t('auth_password_placeholder', '••••••••')}
            aria-invalid={form.formState.errors.password ? true : undefined}
            className="pr-10"
            {...form.register('password')}
          />
          <button
            type="button"
            className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label={
              passwordVisible
                ? t('auth_hide_password', 'Hide password')
                : t('auth_show_password', 'Show password')
            }
            title={
              passwordVisible
                ? t('auth_hide_password', 'Hide password')
                : t('auth_show_password', 'Show password')
            }
            onClick={() => setPasswordVisible((v) => !v)}
          >
            {passwordVisible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        {form.formState.errors.password ? (
          <p className="text-xs text-destructive">
            {t('auth_password_required', 'Password is required.')}
          </p>
        ) : null}
      </div>
      {enableTestUser && showTestWarning ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">
          {t(
            'auth_test_user_warning',
            'You are using a shared test account. Use it for testing only. Do not save sensitive information or personal data.',
          )}
        </p>
      ) : null}
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
      <p className="text-center text-sm text-muted-foreground">
        {t('auth_no_account', "Don't have an account?")}{' '}
        <Link
          to="/signup"
          className="font-medium text-foreground underline-offset-2 hover:underline"
        >
          {t('auth_signup_link', 'Sign up')}
        </Link>
      </p>
    </form>
  );

  return (
    <AuthLayout
      title={t('auth_login_title', 'Sign in')}
      subtitle={t('auth_login_subtitle', 'Sign in to your workspace.')}
    >
      {pendingMfa ? (
        <LoginMfaForm
          pending={pendingMfa}
          onAuthenticated={() => navigate('/chat', { replace: true })}
          onBack={() => {
            setPendingMfa(null);
            form.setValue('password', '');
          }}
        />
      ) : (
        enterpriseSsoEnabled ? (
          <Tabs
            value={loginMethod}
            onValueChange={(value) => setLoginMethod(value as 'email' | 'sso')}
          >
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="email">
                {t('auth_email_tab', 'Email')}
              </TabsTrigger>
              <TabsTrigger value="sso">
                {t('auth_sso_tab', 'Company SSO')}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="email" className="mt-5">
              {emailLogin}
            </TabsContent>
            <TabsContent value="sso" className="mt-5">
              <Suspense fallback={null}>
                <EnterpriseSsoLogin />
              </Suspense>
            </TabsContent>
          </Tabs>
        ) : emailLogin
      )}
    </AuthLayout>
  );
}

import animate from 'tailwindcss-animate';

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        border: 'oklch(var(--border) / <alpha-value>)',
        input: 'oklch(var(--input) / <alpha-value>)',
        ring: 'oklch(var(--ring) / <alpha-value>)',
        background: 'oklch(var(--background) / <alpha-value>)',
        foreground: 'oklch(var(--foreground) / <alpha-value>)',
        primary: {
          DEFAULT: 'oklch(var(--primary) / <alpha-value>)',
          foreground: 'oklch(var(--primary-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'oklch(var(--secondary) / <alpha-value>)',
          foreground: 'oklch(var(--secondary-foreground) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'oklch(var(--destructive) / <alpha-value>)',
          foreground: 'oklch(var(--destructive-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'oklch(var(--muted) / <alpha-value>)',
          foreground: 'oklch(var(--muted-foreground) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'oklch(var(--accent) / <alpha-value>)',
          foreground: 'oklch(var(--accent-foreground) / <alpha-value>)',
        },
        popover: {
          DEFAULT: 'oklch(var(--popover) / <alpha-value>)',
          foreground: 'oklch(var(--popover-foreground) / <alpha-value>)',
        },
        card: {
          DEFAULT: 'oklch(var(--card) / <alpha-value>)',
          foreground: 'oklch(var(--card-foreground) / <alpha-value>)',
        },
        surface: {
          app: 'oklch(var(--surface-app) / <alpha-value>)',
          nav: 'oklch(var(--surface-nav) / <alpha-value>)',
          work: 'oklch(var(--surface-work) / <alpha-value>)',
          view: 'oklch(var(--surface-view) / <alpha-value>)',
          sandbox: 'oklch(var(--surface-sandbox) / <alpha-value>)',
          sunken: 'oklch(var(--surface-sunken) / <alpha-value>)',
          raised: 'oklch(var(--surface-raised) / <alpha-value>)',
          hover: 'oklch(var(--surface-hover) / <alpha-value>)',
        },
        content: {
          primary: 'oklch(var(--text-primary) / <alpha-value>)',
          secondary: 'oklch(var(--text-secondary) / <alpha-value>)',
          tertiary: 'oklch(var(--text-tertiary) / <alpha-value>)',
        },
        edge: {
          structural: 'oklch(var(--edge-structural) / <alpha-value>)',
          subtle: 'oklch(var(--edge-subtle) / <alpha-value>)',
        },
        focus: 'oklch(var(--focus) / <alpha-value>)',
        state: {
          info: 'oklch(var(--state-info) / <alpha-value>)',
          running: 'oklch(var(--state-running) / <alpha-value>)',
          success: 'oklch(var(--state-success) / <alpha-value>)',
          warning: 'oklch(var(--state-warning) / <alpha-value>)',
          danger: 'oklch(var(--state-danger) / <alpha-value>)',
        },
        resource: {
          chat: 'oklch(var(--resource-chat) / <alpha-value>)',
          workflow: 'oklch(var(--resource-workflow) / <alpha-value>)',
          task: 'oklch(var(--resource-task) / <alpha-value>)',
          deployment: 'oklch(var(--resource-deployment) / <alpha-value>)',
          credential: 'oklch(var(--resource-credential) / <alpha-value>)',
          mcp: 'oklch(var(--resource-mcp) / <alpha-value>)',
          skill: 'oklch(var(--resource-skill) / <alpha-value>)',
          knowledge: 'oklch(var(--resource-knowledge) / <alpha-value>)',
          storage: 'oklch(var(--resource-storage) / <alpha-value>)',
          organization: 'oklch(var(--resource-organization) / <alpha-value>)',
          management: 'oklch(var(--resource-management) / <alpha-value>)',
        },
      },
      borderRadius: {
        lg: 'var(--radius-card)',
        md: 'var(--radius-control)',
        sm: 'calc(var(--radius-control) - 2px)',
        floating: 'var(--radius-floating)',
      },
      boxShadow: {
        raised: 'var(--shadow-raised)',
        popover: 'var(--shadow-popover)',
        modal: 'var(--shadow-modal)',
      },
      transitionDuration: {
        feedback: 'var(--motion-feedback)',
        popover: 'var(--motion-popover)',
        dialog: 'var(--motion-dialog)',
        pane: 'var(--motion-pane)',
      },
      zIndex: {
        sticky: '20',
        popover: '40',
        auxiliary: '45',
        modal: '50',
        'modal-popover': '55',
        toast: '60',
      },
      keyframes: {
        // A calm breathing halo indicates a node that is
        // mid-execution. A soft blue box-shadow that swells and recedes over
        // a slow ~1.6s ease-in-out cycle, far gentler than Tailwind's
        // `animate-pulse` opacity blink. The shadow (not opacity) keeps the
        // card itself fully legible while it breathes. Because the cycle is
        // CSS-clock driven, multiple nodes running at once (parallel branches)
        // all breathe in sync — each node's halo is its own element, so they
        // light up independently while sharing one calm rhythm.
        'node-breathe': {
          '0%, 100%': {
            boxShadow: '0 0 0 1px oklch(var(--state-running) / 0.45), 0 0 5px 1px oklch(var(--state-running) / 0.16)',
          },
          '50%': {
            boxShadow: '0 0 0 2px oklch(var(--state-running) / 0.7), 0 0 12px 3px oklch(var(--state-running) / 0.28)',
          },
        },
      },
      animation: {
        'node-breathe': 'node-breathe 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [animate],
};

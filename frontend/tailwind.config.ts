import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      bg: 'var(--bg)',
      sidebar: 'var(--bg-sidebar)',
      subtle: 'var(--bg-subtle)',
      raised: 'var(--bg-raised)',
      line: 'var(--border)',
      'line-strong': 'var(--border-strong)',
      'line-faint': 'var(--border-faint)',
      ink: 'var(--text)',
      secondary: 'var(--text-secondary)',
      muted: 'var(--text-muted)',
      accent: 'var(--accent)',
      'accent-soft': 'var(--accent-soft)',
      'accent-on-soft': 'var(--accent-on-soft)',
      success: 'var(--success)',
      'success-soft': 'var(--success-soft)',
      danger: 'var(--danger)',
      'danger-soft': 'var(--danger-soft)',
      warning: 'var(--warning)',
      'warning-soft': 'var(--warning-soft)',
      primary: 'var(--text)',
      'primary-foreground': 'var(--bg)',
    },
    borderRadius: {
      none: '0',
      sm: 'var(--r-sm)',
      DEFAULT: 'var(--r-md)',
      md: 'var(--r-md)',
      lg: 'var(--r-lg)',
      xl: 'var(--r-xl)',
      full: '9999px',
    },
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        soft: 'var(--shadow-soft)',
      },
      maxWidth: {
        thread: '720px',
      },
    },
  },
  plugins: [],
} satisfies Config;

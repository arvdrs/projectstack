/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#100f0f',
        surface: '#161515',
        elevated: '#1c1b1b',
        border: 'rgba(255,255,255,0.1)',
        'text-primary': '#f5f5f5',
        'text-secondary': '#a3a3a3',
        'text-muted': '#8a8a8a',
        accent: '#de5353',
        'accent-hover': '#c43030',
        'accent-dim': '#ab2020',
        success: '#3ccf7a',
        warning: '#e5a93b',
        danger: '#ff6b6b',
        info: '#6ea8fe',
      },
      fontFamily: {
        sans: ['General Sans', 'Open Sans', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.5s ease-in-out',
        'slide-up': 'slideUp 0.5s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { transform: 'translateY(20px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}

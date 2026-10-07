/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        "meteor-cyan": "var(--meteor-cyan)",
        "sky-blue": "var(--sky-blue)",
        "cloud-blue": "var(--cloud-blue)",
        "cosmic-blue": "var(--cosmic-blue)",
        midnight: "var(--midnight)",
        slate: "var(--slate)",
        "night-sky": "var(--night-sky)",
        "deep-space": "var(--deep-space)",
        panel: "var(--panel)",
        horizon: "var(--horizon)",
        "star-dust": "var(--star-dust)",
        "cloud-white": "var(--cloud-white)",
        mist: "var(--mist)",
        paper: "var(--paper)",
        ember: "var(--ember)",
        aurora: "var(--aurora)",
        link: "var(--link)",
        background: "var(--background)",
        foreground: "var(--foreground)",
        section: "var(--section)",
        field: "var(--field)",
        nav: "var(--nav)",
        overlay: "var(--overlay)",
        sent: "var(--sent)",
        "sent-border": "var(--sent-border)",
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
        primary: {
          DEFAULT: "var(--primary)",
          foreground: "var(--primary-foreground)",
        },
        secondary: {
          DEFAULT: "var(--secondary)",
          foreground: "var(--secondary-foreground)",
          hover: "var(--secondary-hover)",
        },
        muted: {
          DEFAULT: "var(--muted)",
          foreground: "var(--muted-foreground)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          foreground: "var(--accent-foreground)",
        },
        card: {
          DEFAULT: "var(--card)",
          foreground: "var(--card-foreground)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        glow: "var(--glow)",
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "Segoe UI", "sans-serif"],
        display: ["IBM Plex Sans", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};

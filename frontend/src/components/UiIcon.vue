<script setup lang="ts">
export type IconName =
  | 'tickets'
  | 'shield'
  | 'trace'
  | 'chart'
  | 'arrow'
  | 'plus'
  | 'refresh'
  | 'logout'
  | 'check'
  | 'book'
  | 'lock'
defineProps<{ name: IconName; size?: number }>()
const paths: Record<IconName, string> = {
  tickets: 'M4 5h16v5a2 2 0 0 0 0 4v5H4v-5a2 2 0 0 0 0-4V5Zm11 3v2m0 4v2',
  shield: 'M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6l-8-3Zm-4 9 3 3 5-6',
  trace: 'M6 7v10m6-10v10m6-10v10M3 7h18M3 17h18M6 4v1m6 14v1m6-16v1',
  chart: 'M4 4v16h17M8 16v-4m5 4V7m5 9v-7',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  plus: 'M12 5v14M5 12h14',
  refresh: 'M20 8a8 8 0 1 0 0 8M20 3v5h-5',
  logout: 'M9 5H4v14h5m5-14 5 7-5 7M8 12h11',
  check: 'm5 12 4 4L19 6',
  book: 'M12 6v14M3 4c4-1 7 0 9 2 2-2 5-3 9-2v14c-4-1-7 0-9 2-2-2-5-3-9-2V4Z',
  lock: 'M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5V10Zm7 4v3',
}
</script>
<template>
  <svg
    :width="size || 20"
    :height="size || 20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name]" />
  </svg>
</template>

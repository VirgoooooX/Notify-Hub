import { ref } from 'vue'

export function useAsyncAction() {
  const pending = ref(false)

  async function run<T>(action: () => Promise<T>): Promise<T> {
    pending.value = true
    try {
      return await action()
    } finally {
      pending.value = false
    }
  }

  return { pending, run }
}

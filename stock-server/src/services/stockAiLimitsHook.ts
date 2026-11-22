import { useEffect, useState } from 'react'
import {
  type StockAssistantAILimits,
  currentLimits,
  statusListeners,
} from './stockAiLimits.js'

export function useStockAssistantAiLimits(): StockAssistantAILimits {
  const [limits, setLimits] = useState<StockAssistantAILimits>({ ...currentLimits })

  useEffect(() => {
    const listener = (newLimits: StockAssistantAILimits) => {
      setLimits({ ...newLimits })
    }
    statusListeners.add(listener)

    return () => {
      statusListeners.delete(listener)
    }
  }, [])

  return limits
}

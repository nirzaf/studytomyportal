import Image from 'next/image'

export interface StudytomyAiMarkProps {
  size?: number
  className?: string
  decorative?: boolean
}

export function StudytomyAiMark({
  size = 20,
  className,
  decorative = true,
}: StudytomyAiMarkProps) {
  return (
    <Image
      src="/studytomy-mini.png"
      width={size}
      height={size}
      alt={decorative ? '' : 'Studytomy AI assistant'}
      aria-hidden={decorative || undefined}
      className={className}
    />
  )
}

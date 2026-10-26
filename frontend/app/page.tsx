import Link from "next/link";
import Image from "next/image";
import { CONTACT_EMAIL, PRODUCT_NAME } from "@/lib/branding";

const steps = [
  {
    title: "Подключите encoder",
    text: "Скопируйте RTMP server и stream key в OBS, vMix или аппаратный кодер.",
  },
  {
    title: "Выберите площадки",
    text: "Добавьте YouTube, VK, Rutube, Telegram, Twitch или любой RTMP endpoint.",
  },
  {
    title: "Запустите один эфир",
    text: `${PRODUCT_NAME} принимает входящий поток и одновременно отправляет его во все выбранные направления.`,
  },
];

const features = [
  "Один исходящий поток из студии вместо отдельных настроек под каждую платформу",
  "Отдельный live-статус по каждой площадке: видно, где эфир идет, а где нужен перезапуск",
  "Автовосстановление FFmpeg worker при сетевых сбоях и обрывах destination",
  "Профили площадок: сохраненные RTMP URL и stream key без повторного ввода",
  "HLS-preview в кабинете для быстрой проверки входящего сигнала",
  "Уведомления о старте, остановке и проблемах доставки",
];

const audience = [
  "event-команд и организаторов конференций",
  "продакшн-студий и технических подрядчиков",
  "блогеров, экспертов и онлайн-школ",
  "компаний, которым нужен единый контроль multistreaming",
];

const faqs = [
  {
    question: "Нужно менять OBS или vMix?",
    answer: "Нет. Подойдет любой encoder, который умеет отправлять RTMP-поток.",
  },
  {
    question: "Какие площадки можно подключить?",
    answer: "YouTube, VK, Rutube, Telegram, Twitch и custom RTMP/RTMPS endpoints.",
  },
  {
    question: "Что с тарифами?",
    answer: "Тарифы появятся позже. Сейчас продукт готовится к beta-тесту.",
  },
  {
    question: "Сервис транскодирует видео?",
    answer: "Текущая версия работает как relay: принимает один поток и передает его дальше без изменения качества.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <Link className="landing-brand" href="/">
          <Image src="/logo.png" alt={PRODUCT_NAME} width={287} height={34} priority />
        </Link>
        <nav>
          <a href="#how">Как работает</a>
          <a href="#features">Возможности</a>
          <a href="#faq">FAQ</a>
        </nav>
        <Link className="button secondary landing-login" href="/dashboard">
          Войти в личный кабинет
        </Link>
      </header>

      <section className="landing-hero">
        <div className="hero-copy">
          <span className="eyebrow">Платформа для multistreaming</span>
          <div className="hero-badges" aria-label="Ключевые возможности">
            <span>OBS / vMix</span>
            <span>RTMP / RTMPS</span>
            <span>YouTube · VK · Rutube · Telegram · Twitch</span>
          </div>
          <h1 className="hero-title">
            Один поток —
            <span> все площадки одновременно</span>
          </h1>
          <p>
            Запускайте трансляцию один раз из OBS, vMix или аппаратного кодера.
            Платформа доставит поток на нужные сервисы и покажет состояние каждого направления.
          </p>
          <div className="hero-actions">
            <Link className="button" href="/dashboard">
              Начать трансляцию
            </Link>
            <Link className="button secondary" href="/dashboard">
              Войти в кабинет
            </Link>
          </div>
          <p className="hero-note">Beta-версия. Тарифы и публичная регистрация будут объявлены отдельно.</p>
        </div>
        <div className="hero-card" aria-label="Схема рестрима">
          <div className="signal-card source">OBS / vMix</div>
          <div className="signal-line" />
          <div className="signal-card core">{PRODUCT_NAME}</div>
          <div className="destinations-cloud">
            <span>YouTube</span>
            <span>VK</span>
            <span>Rutube</span>
            <span>Telegram</span>
            <span>Twitch</span>
          </div>
        </div>
      </section>

      <section className="landing-section" id="how">
        <div className="section-heading">
          <span className="eyebrow">Как это работает</span>
          <h2>Три действия перед live-эфиром</h2>
        </div>
        <div className="landing-grid three">
          {steps.map((step, index) => (
            <article className="landing-card" key={step.title}>
              <span className="step-number">{index + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="features">
        <div className="section-heading">
          <span className="eyebrow">Возможности MVP</span>
          <h2>Контроль доставки без лишней операционной рутины</h2>
        </div>
        <div className="feature-list">
          {features.map((feature) => (
            <div className="feature-item" key={feature}>
              <span>✓</span>
              <p>{feature}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section split">
        <div>
          <span className="eyebrow">Для кого</span>
          <h2>Для команд, которым нужно вещать сразу на несколько платформ</h2>
          <p>
            Продукт подходит для регулярных эфиров, мероприятий, запусков, конференций,
            онлайн-курсов и студийного производства. Главное — один стабильный входящий
            поток и понятный контроль исходящих направлений.
          </p>
        </div>
        <ul className="audience-list">
          {audience.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="landing-section" id="faq">
        <div className="section-heading">
          <span className="eyebrow">FAQ</span>
          <h2>Коротко о запуске и ограничениях</h2>
        </div>
        <div className="faq-list">
          {faqs.map((faq) => (
            <article className="faq-item" key={faq.question}>
              <h3>{faq.question}</h3>
              <p>{faq.answer}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section contact-section" id="contact">
        <div>
          <span className="eyebrow">Контакт</span>
          <h2>Хотите протестировать multistreaming на своем эфире?</h2>
          <p>
            Напишите, какие площадки нужны, какой encoder используете и какой ожидается битрейт.
            Подскажем, как подготовить beta-тест.
          </p>
        </div>
        <div className="contact-actions">
          <a className="button" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          <Link className="button secondary" href="/dashboard">
            Личный кабинет
          </Link>
        </div>
      </section>

      <footer className="landing-footer">
        <Image src="/logo.png" alt={PRODUCT_NAME} width={254} height={30} />
        <span>Multistreaming platform</span>
      </footer>
    </main>
  );
}

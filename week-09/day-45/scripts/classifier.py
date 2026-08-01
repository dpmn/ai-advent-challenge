#!/usr/bin/env python3
"""Первая ступень: классификатор категории без языковой модели.

Устройство простое и целиком арифметическое.

1. Название режется на буквосочетания по 3–5 символов. Каждое становится
   признаком, вес считается формулой: сколько раз встретилось в названии,
   помноженное на редкость по всей обучающей выборке. Частые куски («для»,
   «ая ») гасятся сами, списка стоп-слов не нужно.
2. Обученная таблица весов «буквосочетание × категория» превращает эти числа
   в 23 оценки, а те — в доли, дающие в сумме единицу. Наибольшая доля и есть
   уверенность, которую требует задание.

Буквосочетания, а не слова целиком, — из-за данных дня 41: в них полно
обрезанных названий («для сухи», «(Бел») и разных окончаний одного слова.
По кускам это ловится, по словам целиком — нет.

Порог уверенности подбирается **на обучающей выборке** перекрёстной проверкой:
400 примеров делятся на пять частей, модель по очереди учится на четырёх
и предсказывает пятую. Так оценки получены на примерах, которых модель
не видела. Подбирать порог по проверочной сотне значило бы настроить систему
по тому самому числу, которым она потом отчитывается.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict
from sklearn.pipeline import make_pipeline

from common import MODEL_PATH, load_eval, load_train

# Длина буквосочетаний. Нижняя граница 3 — короче куски перестают что-либо
# значить; верхняя 5 — длиннее они уже почти слова и перестают ловить
# обрезанные названия.
NGRAM_RANGE = (3, 5)

# Буквосочетание должно встретиться минимум в двух названиях. Единичные —
# это шум вроде артикулов, и они только раздувают таблицу весов.
MIN_DF = 2

# Сила ограничения на величину весов. Чем меньше число, тем скромнее веса
# и тем сильнее защита от зубрёжки редких примеров (в выборке есть категории
# с пятью примерами). Значение по умолчанию библиотеки — 1.0.
REGULARIZATION = 4.0

FOLDS = 5

# Порог берётся как наименьший, при котором доля верных среди оставленных
# классификатору достигает этой планки. Смысл: первая ступень имеет право
# отвечать сама, только пока она права хотя бы в 85% случаев.
TARGET_PRECISION = 0.85

THRESHOLD_SWEEP = [0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


def build_model() -> object:
    """Собирает связку «нарезка на буквосочетания → таблица весов»."""
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM_RANGE, min_df=MIN_DF,
                        sublinear_tf=True),
        LogisticRegression(max_iter=2000, C=REGULARIZATION),
    )


class CategoryClassifier:
    """Обученный классификатор категории: предсказание плюс уверенность."""

    def __init__(self, model, threshold: float) -> None:
        self.model = model
        self.threshold = threshold

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "CategoryClassifier":
        """Читает обученную модель и подобранный порог с диска."""
        if not path.exists():
            raise FileNotFoundError(
                f"модель не найдена: {path}. Сначала запусти "
                "`classifier.py --train` и `classifier.py --calibrate`")
        payload = joblib.load(path)
        return cls(payload["model"], payload["threshold"])

    def save(self, path: Path = MODEL_PATH) -> None:
        """Сохраняет модель и порог одним файлом."""
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "threshold": self.threshold}, path)

    def predict(self, name: str) -> dict:
        """Категория, уверенность и три лучших варианта для одного названия."""
        probabilities = self.model.predict_proba([name])[0]
        labels = list(self.model.classes_)
        ranked = sorted(zip(labels, probabilities), key=lambda pair: -pair[1])
        best, confidence = ranked[0]
        return {
            "category": best,
            "confidence": round(float(confidence), 4),
            "status": "OK" if confidence >= self.threshold else "UNSURE",
            "top": [{"category": label, "p": round(float(p), 4)} for label, p in ranked[:3]],
        }


def train(verbose: bool = True) -> CategoryClassifier:
    """Обучает классификатор на 400 примерах дня 41."""
    records = load_train()
    names = [r["name"] for r in records]
    labels = [r["category"] for r in records]

    model = build_model()
    model.fit(names, labels)

    if verbose:
        vectorizer = model.named_steps["tfidfvectorizer"]
        print(f"обучено на {len(records)} примерах · "
              f"{len(vectorizer.vocabulary_)} буквосочетаний · "
              f"{len(model.classes_)} категорий")
    return CategoryClassifier(model, threshold=0.0)


def calibrate(target: float = TARGET_PRECISION, verbose: bool = True) -> float:
    """Подбирает порог уверенности перекрёстной проверкой на train.

    Возвращает наименьший порог, при котором доля верных среди оставленных
    классификатору достигает `target`. Если такого нет — берётся порог
    с наибольшей долей верных.
    """
    records = load_train()
    names = [r["name"] for r in records]
    labels = [r["category"] for r in records]

    model = build_model()
    probabilities = cross_val_predict(model, names, labels, cv=FOLDS, method="predict_proba")
    # Порядок столбцов в оценках перекрёстной проверки совпадает с порядком
    # классов у модели, обученной на всей выборке, — он нужен, чтобы понять,
    # какому столбцу какая категория соответствует.
    model.fit(names, labels)
    classes = list(model.classes_)

    rows = []
    for threshold in THRESHOLD_SWEEP:
        kept = correct = 0
        for probability, truth in zip(probabilities, labels):
            best_index = max(range(len(classes)), key=lambda i: probability[i])
            if probability[best_index] < threshold:
                continue
            kept += 1
            correct += classes[best_index] == truth
        precision = correct / kept if kept else 0.0
        rows.append({
            "threshold": threshold,
            "kept": kept,
            "kept_share": kept / len(labels),
            "precision": precision,
        })

    good = [row for row in rows if row["precision"] >= target and row["kept"]]
    chosen = good[0]["threshold"] if good else max(rows, key=lambda r: r["precision"])["threshold"]

    if verbose:
        print(f"\nПодбор порога на {len(labels)} обучающих примерах "
              f"({FOLDS} частей, каждая предсказана моделью, которая её не видела)\n")
        print("| порог | оставлено классификатору | доля от всех | верных среди оставленных |")
        print("|---|---|---|---|")
        for row in rows:
            mark = " ←" if row["threshold"] == chosen else ""
            print(f"| {row['threshold']:.2f} | {row['kept']} | {row['kept_share'] * 100:.0f}% | "
                  f"{row['precision'] * 100:.1f}%{mark} |")
        print(f"\nВыбран порог {chosen:.2f}: наименьший, при котором классификатор прав "
              f"хотя бы в {target * 100:.0f}% случаев из тех, что берёт на себя.")

    return chosen


def show_eval_preview(classifier: CategoryClassifier) -> None:
    """Печатает, как классификатор в одиночку справляется с проверочной сотней.

    Это опорный замер «всё решает классификатор», а не настройка: порог уже
    выбран на train и здесь не трогается.
    """
    records = load_eval()
    correct = sum(classifier.predict(r["name"])["category"] == r["category"] for r in records)
    confident = sum(classifier.predict(r["name"])["status"] == "OK" for r in records)
    print(f"\nПроверочная сотня без большой модели: верных {correct}/{len(records)} "
          f"({correct / len(records) * 100:.1f}%), уверенных ответов {confident}/{len(records)}")


def main() -> None:
    """CLI: обучение, подбор порога, разовое предсказание."""
    parser = argparse.ArgumentParser(description="Классификатор категории (первая ступень)")
    parser.add_argument("--train", action="store_true", help="обучить и сохранить модель")
    parser.add_argument("--calibrate", action="store_true", help="подобрать порог на train")
    parser.add_argument("--target", type=float, default=TARGET_PRECISION,
                        help="какая доля верных нужна среди оставленных классификатору")
    parser.add_argument("--predict", metavar="НАЗВАНИЕ", help="предсказать категорию одной строки")
    parser.add_argument("--preview", action="store_true",
                        help="показать, как классификатор один справляется с eval")
    args = parser.parse_args()

    if args.train or args.calibrate:
        classifier = train()
        classifier.threshold = calibrate(args.target) if args.calibrate else 0.0
        classifier.save()
        print(f"\nсохранено: {MODEL_PATH} (порог {classifier.threshold:.2f})")
        if args.preview:
            show_eval_preview(classifier)
        return

    classifier = CategoryClassifier.load()
    if args.predict:
        result = classifier.predict(args.predict)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.preview:
        show_eval_preview(classifier)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

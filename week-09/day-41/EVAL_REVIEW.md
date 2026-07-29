# Вычитка eval

Всего примеров: **100**. Проверить нужно все —
ошибка в эталоне eval наказывает модель за правильный ответ.

На что смотреть в первую очередь:

- **`brand` vs `line` vs `shade`** — главная неоднозначность. Строка в кавычках
  не обязана быть брендом, а сам бренд часто стоит в скобках в конце.
- **`volume`** — объём ОДНОЙ единицы, не суммарный по упаковке.
- **`is_set`** — true только для набора из РАЗНЫХ продуктов.
- **`category`** — если товар не лёг ни в один узел, должно быть `прочее`.

Нашёл ошибку — правь `datasets/eval.jsonl` в поле `messages[2].content`
и прогони `python3 scripts/validate.py`.

---

### 1. `p01340`

**Маска-патчи тканевые**

[карточка товара](https://www.wildberries.ru/catalog/559323770/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`маска_для_лица`

```json
{
  "category": "маска_для_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 2. `p01737`

**Порошок для стирки белья с кислородом**

[карточка товара](https://www.wildberries.ru/catalog/544223820/detail.aspx?root=555070161)

brand=`None` · line=`None` · shade=`None` · category=`средство_для_стирки`

```json
{
  "category": "средство_для_стирки",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "порошок",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 3. `p01747`

**Профессиональная окисляющая эмульсия 20 VOL оксид 6%**

[карточка товара](https://www.wildberries.ru/catalog/542404740/detail.aspx?root=553243156)

brand=`None` · line=`None` · shade=`None` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "эмульсия",
  "purpose": [
    "окрашивание"
  ],
  "shade": null,
  "is_set": false
}
```

### 4. `p02349`

**Шампунь для волос Volume booster объёма 1 л**

[карточка товара](https://www.wildberries.ru/catalog/560196840/detail.aspx?root=573248841)

ловушки: `dirty_unit`

brand=`None` · line=`Volume booster` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": null,
  "line": "Volume booster",
  "volume": {
    "value": 1000,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "объем"
  ],
  "shade": null,
  "is_set": false
}
```

### 5. `p01863`

**Себонормализующий шампунь для жирной кожи головы и ослабленных волос, 1000 мл**

[карточка товара](https://www.wildberries.ru/catalog/559485738/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": null,
  "line": null,
  "volume": {
    "value": 1000,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "против_жира",
    "укрепление"
  ],
  "shade": null,
  "is_set": false
}
```

### 6. `p01919`

**спрей для волос никотиновая кислота 2% 100 мл - 3 шт**

[карточка товара](https://www.wildberries.ru/catalog/548282654/detail.aspx?root=560013616)

ловушки: `pack`, `multi_volume`

brand=`None` · line=`None` · shade=`None` · category=`стайлинг`

```json
{
  "category": "стайлинг",
  "brand": null,
  "line": null,
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 3,
  "form": "спрей",
  "purpose": [
    "против_выпадения"
  ],
  "shade": null,
  "is_set": false
}
```

### 7. `p02142`

**Сыворотка для роста волос и ресниц**

[карточка товара](https://www.wildberries.ru/catalog/545134660/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "сыворотка",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 8. `p00631`

**Бальзам для губ питательный пантенол, масло ши, от сухости губ увлажняющий, гигиеническая помада MAXCARE, Вишня**

[карточка товара](https://www.ozon.ru/products/523246647/?at=Rltyp7GLjIqqNDgqH9Yvl1GSA30oZvCp7lzk6Sr88KMJ)

brand=`MAXCARE` · line=`None` · shade=`Вишня` · category=`средство_для_губ`

```json
{
  "category": "средство_для_губ",
  "brand": "MAXCARE",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "бальзам",
  "purpose": [
    "питание",
    "увлажнение"
  ],
  "shade": "Вишня",
  "is_set": false
}
```

### 9. `p01203`

**Крем против загара Floresan отбеливающий SPF 35, 125 мл**

[карточка товара](https://www.wildberries.ru/catalog/559974551/detail.aspx)

brand=`Floresan` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Floresan",
  "line": null,
  "volume": {
    "value": 125,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "крем",
  "purpose": [
    "против_пигментации",
    "защита_от_солнца"
  ],
  "shade": null,
  "is_set": false
}
```

### 10. `p01667`

**Пластины от комаров для фумигатора 100 штук**

[карточка товара](https://www.ozon.ru/products/2078815986/?at=oZt6A3EyjFvgK6AwcBrGB49CG3JyE3tVnmmD1fJzzG3P&region=msk)

ловушки: `pack`

brand=`None` · line=`None` · shade=`None` · category=`дезинсекция`

```json
{
  "category": "дезинсекция",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 100,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 11. `p00192`

**Eucerin Hyaluron-Filler+Elasticity Крем для дневного ухода за кожей банка 50 мл 1 шт**

[карточка товара](https://www.wildberries.ru/catalog/533414658/detail.aspx?root=541600701)

ловушки: `pack`, `set_like`, `multi_volume`

brand=`Eucerin` · line=`Hyaluron-Filler+Elasticity` · shade=`None` · category=`уход_за_лицом`

```json
{
  "category": "уход_за_лицом",
  "brand": "Eucerin",
  "line": "Hyaluron-Filler+Elasticity",
  "volume": {
    "value": 50,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "крем",
  "purpose": [
    "антивозрастное"
  ],
  "shade": null,
  "is_set": false
}
```

### 12. `p01410`

**Мусс для умывания лица с алоэ вера 30+ 40+ 50+**

[карточка товара](https://www.wildberries.ru/catalog/886660661/detail.aspx?root=1028563723)

ловушки: `set_like`

brand=`None` · line=`None` · shade=`None` · category=`очищение_лица`

```json
{
  "category": "очищение_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "мусс",
  "purpose": [
    "очищение",
    "увлажнение",
    "антивозрастное"
  ],
  "shade": null,
  "is_set": false
}
```

### 13. `p01373`

**Масло от растяжек Восстановление & питание, 200 мл**

[карточка товара](https://www.wildberries.ru/catalog/559200007/detail.aspx?root=572289388)

brand=`None` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": null,
  "line": null,
  "volume": {
    "value": 200,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "масло",
  "purpose": [
    "восстановление",
    "питание"
  ],
  "shade": null,
  "is_set": false
}
```

### 14. `p00377`

**Mon Platin Нежное масло для младенцев 250 мл**

[карточка товара](https://www.ozon.ru/product/mon-platin-nezhnoe-maslo-dlya-mladentsev-250-ml-153985805/?asb=EUnlvZ%252B1Wb0j4VtZbeJT9NBpwZ79lX%252BAI%252FCcLys8AgvM5PMDjSKoEeLLA9Iww9m5&asb2=0c87x06t3l0_rCM6ISIerLAxe9t6EE_3ZEiE5JMlsWePKfYkO__EuCBv_wHZ8ueXr1mLPsmbPZOeE3RmeJ0kQt7jk89xODOJfJLutBugh1CXZZLuR48BMPYhlZmOh5O38Vzf6k6PBtyzz9TpcHSwKQ&avtc=1&avte=2&avts=1669333753)

brand=`Mon Platin` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Mon Platin",
  "line": null,
  "volume": {
    "value": 250,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "масло",
  "purpose": [
    "детское"
  ],
  "shade": null,
  "is_set": false
}
```

### 15. `p02336`

**Шампунь глубоко очищающий и укрепляющий**

[карточка товара](https://www.wildberries.ru/catalog/884852456/detail.aspx?root=1022887209)

brand=`None` · line=`None` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "очищение",
    "укрепление"
  ],
  "shade": null,
  "is_set": false
}
```

### 16. `p01371`

**Масло косметическое зверобой 10мл**

[карточка товара](https://www.wildberries.ru/catalog/559939652/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": null,
  "line": null,
  "volume": {
    "value": 10,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "масло",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 17. `p00393`

**NOMI / Подарочный набор детской декоративной косметики для девочек "Фиолетовая мечта"**

[карточка товара](https://www.ozon.ru/product/nabor-detskoy-kosmetiki-nomi-fioletovaya-mechta-149394617/?asb=jH6vPbL5KCd1rvOHbOEKQRzjfJYv1sOH6Iv4AJFMSqA%253D&asb2=3KY5m3Q3KAqKZwCJAVDgRNGN5jvk7F0oNoQNcQ75nuWjFIiDIg_L4cvymYEHfiSA&avtc=1&avte=2&avts=1670282251)

ловушки: `quoted`, `set_like`

brand=`NOMI` · line=`None` · shade=`None` · category=`декоративная_косметика`

```json
{
  "category": "декоративная_косметика",
  "brand": "NOMI",
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 18. `p01048`

**Краска Koleston Perfect 7/71**

[карточка товара](https://www.wildberries.ru/catalog/559467160/detail.aspx)

ловушки: `shade_code`

brand=`Koleston` · line=`Perfect` · shade=`7/71` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": "Koleston",
  "line": "Perfect",
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": "7/71",
  "is_set": false
}
```

### 19. `p00533`

**URIAGE Bebe 1 Ere Детская очищающая вода для гигиены новорожденных, 500 мл**

[карточка товара](https://www.ozon.ru/products/523439117/?at=pZtp25zoXcGVBZAkIrRvKYMinyEM11cNyL9XzhZv4NKg)

brand=`URIAGE` · line=`Bebe 1 Ere` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "URIAGE",
  "line": "Bebe 1 Ere",
  "volume": {
    "value": 500,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "жидкость",
  "purpose": [
    "очищение",
    "детское"
  ],
  "shade": null,
  "is_set": false
}
```

### 20. `p01213`

**Крем-краска farmavita 8.4 светлый медный блондин**

[карточка товара](https://www.wildberries.ru/catalog/884461868/detail.aspx)

ловушки: `shade_code`

brand=`farmavita` · line=`None` · shade=`8.4 светлый медный блондин` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": "farmavita",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "крем",
  "purpose": [
    "окрашивание"
  ],
  "shade": "8.4 светлый медный блондин",
  "is_set": false
}
```

### 21. `p01113`

**Краска для Волос тон 8/34 100мл Diapason**

[карточка товара](https://www.wildberries.ru/catalog/546298467/detail.aspx?root=557166217)

ловушки: `shade_code`

brand=`Diapason` · line=`None` · shade=`8/34` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": "Diapason",
  "line": null,
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": "8/34",
  "is_set": false
}
```

### 22. `p01119`

**Краска для обуви из замши и нубука SALTON Для обновления цвета, черная, 250мл**

[карточка товара](https://www.utkonos.ru/item/073687/kraska-dobuvi-salton-dzamshi-i-nubuka-chernyj-250ml?offerId=073687)

brand=`SALTON` · line=`None` · shade=`черная` · category=`прочее`

```json
{
  "category": "прочее",
  "brand": "SALTON",
  "line": null,
  "volume": {
    "value": 250,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": "черная",
  "is_set": false
}
```

### 23. `p01364`

**Масло для волос, восстанавливает и питает от сухости**

[карточка товара](https://www.wildberries.ru/catalog/542674345/detail.aspx?root=553590477)

brand=`None` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "масло",
  "purpose": [
    "восстановление",
    "питание"
  ],
  "shade": null,
  "is_set": false
}
```

### 24. `p01642`

**Пенка для удаления волос с увлажняющим эффектом**

[карточка товара](https://www.wildberries.ru/catalog/883255065/detail.aspx?root=1018730855)

brand=`None` · line=`None` · shade=`None` · category=`депиляция`

```json
{
  "category": "депиляция",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "пенка",
  "purpose": [
    "увлажнение"
  ],
  "shade": null,
  "is_set": false
}
```

### 25. `p01955`

**Спрей краска для волос оранжевая временная для прядей**

[карточка товара](https://www.wildberries.ru/catalog/884381403/detail.aspx)

brand=`None` · line=`None` · shade=`оранжевая` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "спрей",
  "purpose": [
    "окрашивание",
    "тонирование"
  ],
  "shade": "оранжевая",
  "is_set": false
}
```

### 26. `p00633`

**Бальзам для губ увлажняющий набор новогодний детский**

[карточка товара](https://www.wildberries.ru/catalog/883163416/detail.aspx)

ловушки: `set_like`

brand=`None` · line=`None` · shade=`None` · category=`средство_для_губ`

```json
{
  "category": "средство_для_губ",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "бальзам",
  "purpose": [
    "увлажнение",
    "детское"
  ],
  "shade": null,
  "is_set": false
}
```

### 27. `p00863`

**ГЛИТТЕРИКА Глиттер 1 шт., 100 мл./ 75 г.**

[карточка товара](https://www.ozon.ru/products/1144820873/?at=oZt6Gn3VBh67z7NpiPvEnvBCG9xlPfgnW47lUgmoL8q)

ловушки: `dirty_unit`, `pack`, `multi_volume`

brand=`ГЛИТТЕРИКА` · line=`None` · shade=`None` · category=`декоративная_косметика`

```json
{
  "category": "декоративная_косметика",
  "brand": "ГЛИТТЕРИКА",
  "line": null,
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 28. `p01275`

**М1 Набор слайдеров для ногтей (винтаж, девушки, лисички). Парфюм, Коктейли, Море, Ракушки, Париж, Мода**

[карточка товара](https://www.ozon.ru/product/m1-nabor-slayderov-dlya-nogtey-vintazh-devushki-lisichki-parfyum-kokteyli-more-rakushki-parizh-moda-523043141/?advert=EBI8OEYO7XCGmbxOxB4Dic4m4ReL3fqgfu9eS9XDKX2YBHtngpjoDa9wTdbIYVAjV8C4Vl8jJxZxrzDisln3UoRGmSW0NZosu3w380SIUq8ub7qzR0Bdne79DuJ_3rOcE8ev0YzYJz0UNgGJcBcvghzZio39VWJhmN12a84RykHsOGEEIhM5z9_HDkalT6Hetr6GQq7Bj1CiLrhwnH4m5AGGefQucyvC2M_99IiVmxBUiITooiPzp9Q&avtc=1&avte=2&avts=1685053356)

ловушки: `set_like`

brand=`None` · line=`None` · shade=`None` · category=`расходники_бьюти`

```json
{
  "category": "расходники_бьюти",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 29. `p00576`

**АКС Дезодорант мужской Сила технологий, 150 мл**

[карточка товара](https://www.ozon.ru/products/3119801327/?at=OgtEQQgZzc5B2yDzTR824vzCOEDBgoIpMRD49ukRXEXR)

brand=`АКС` · line=`Сила технологий` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "АКС",
  "line": "Сила технологий",
  "volume": {
    "value": 150,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 30. `p01552`

**Ополаскиватель для посудомоечных машин Rossinka, 500 мл**

[карточка товара](https://www.ozon.ru/products/161317851/?at=nRtrEgRqDF5Y325ki79PJRQfYWXlPYhmZkQ91UrYyrzD)

brand=`Rossinka` · line=`None` · shade=`None` · category=`средство_для_посуды`

```json
{
  "category": "средство_для_посуды",
  "brand": "Rossinka",
  "line": null,
  "volume": {
    "value": 500,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 31. `p01181`

**Крем для удаления волос мужской депилятор 100 г**

[карточка товара](https://www.wildberries.ru/catalog/884205426/detail.aspx)

ловушки: `dirty_unit`

brand=`None` · line=`None` · shade=`None` · category=`депиляция`

```json
{
  "category": "депиляция",
  "brand": null,
  "line": null,
  "volume": {
    "value": 100,
    "unit": "g"
  },
  "pack_count": 1,
  "form": "крем",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 32. `p02052`

**Средство от комаров и клещей спрей для тела Picnic Bio Active 125 мл**

[карточка товара](https://www.ozon.ru/products/150891285/?at=A6tGQGPkQcMny5qJU386R5jFjAyGRQfVLXEKyIOyZvJ2)

brand=`Picnic` · line=`Bio Active` · shade=`None` · category=`прочее`

```json
{
  "category": "прочее",
  "brand": "Picnic",
  "line": "Bio Active",
  "volume": {
    "value": 125,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "спрей",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 33. `p01641`

**Пенка для купания и игр в ванной детская Моя Прелесть Воздушная зефирка 200 мл**

[карточка товара](https://www.ozon.ru/products/160931370/?at=Rlty6NrW7FQvg3nPh3Dl0MxCJ5k6NpFVyYBzJTQQAyD0)

brand=`Моя Прелесть` · line=`Воздушная зефирка` · shade=`None` · category=`гель_для_душа`

```json
{
  "category": "гель_для_душа",
  "brand": "Моя Прелесть",
  "line": "Воздушная зефирка",
  "volume": {
    "value": 200,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "пена",
  "purpose": [
    "детское"
  ],
  "shade": null,
  "is_set": false
}
```

### 34. `p00620`

**Бальзам для волос Professional Organic Oil Cold Blond 250мл**

[карточка товара](https://www.wildberries.ru/catalog/884620594/detail.aspx)

ловушки: `truncated`

brand=`None` · line=`None` · shade=`Cold Blond` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": null,
  "volume": {
    "value": 250,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "бальзам",
  "purpose": [],
  "shade": "Cold Blond",
  "is_set": false
}
```

### 35. `p02440`

**Эссенция для лица с экстрактом авокадо против морщин**

[карточка товара](https://www.wildberries.ru/catalog/883654505/detail.aspx?root=1019715083)

brand=`None` · line=`None` · shade=`None` · category=`уход_за_лицом`

```json
{
  "category": "уход_за_лицом",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "антивозрастное"
  ],
  "shade": null,
  "is_set": false
}
```

### 36. `p01564`

**Освежитель воздуха AIR WICK Pure 5 Эфирных Масел с ароматом апельсина и грейпфрута, 250мл**

[карточка товара](https://www.utkonos.ru/item/457961/osvezhitel-vozdukha-airwick-pure-5-efirnykh-masel-s-aromatom-apelsina-i-grejpfruta-250ml?offerId=457961)

brand=`AIR WICK` · line=`Pure 5 Эфирных Масел` · shade=`апельсина и грейпфрута` · category=`освежитель_аромат`

```json
{
  "category": "освежитель_аромат",
  "brand": "AIR WICK",
  "line": "Pure 5 Эфирных Масел",
  "volume": {
    "value": 250,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "против_запаха"
  ],
  "shade": "апельсина и грейпфрута",
  "is_set": false
}
```

### 37. `p01606`

**Очиститель для обуви SALTON Антисоль от солевых разводов и реагентов, 100мл**

[карточка товара](https://www.utkonos.ru/item/331375/ochistitel-dobuvi-salton-antisol-ochist-razvodov-ot-soli-i-reagentovstiker-1-100ml?offerId=331375)

brand=`SALTON` · line=`Антисоль` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": "SALTON",
  "line": "Антисоль",
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 38. `p02135`

**Сыворотка для лица с витамином C 5% 25мл**

[карточка товара](https://www.wildberries.ru/catalog/559943981/detail.aspx?root=573014495)

brand=`None` · line=`None` · shade=`None` · category=`уход_за_лицом`

```json
{
  "category": "уход_за_лицом",
  "brand": null,
  "line": null,
  "volume": {
    "value": 25,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "сыворотка",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 39. `p01462`

**Набор детских татуировок перевоок "Мари" Коты аристократы**

[карточка товара](https://www.ozon.ru/product/nabor-detskih-tatuirovok-perevook-mari-koty-aristokraty-522891397/?asb=QmjnH13%252B83fUPIGg9cBgMh7uC1q1Hk6HDGVYARZqDa0g%252FQaK62JTiIPThikAteos&asb2=fB1PRYNGbUKC_AEECe15UKeQRS0R9q9kezP6g6VnUeDi9rcA_Aq-vzNn_PxUP5nHQ_qg9G0FGOj_fotSb4LvKMWzP_KnPQWTkOfv90eAigVi75O4r-NsFAcCJ37Gox2SG2zQT8Uph8ratU9S2PYWSw&avtc=1&avte=2&avts=1668813418)

ловушки: `quoted`, `set_like`

brand=`None` · line=`None` · shade=`None` · category=`прочее`

```json
{
  "category": "прочее",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 40. `p00188`

**ESTEL PROFESSIONAL Оксигент DELUXE для окрашивания волос, 9%, 1000 мл**

[карточка товара](https://www.ozon.ru/products/171576026/?at=PjtJwQ4XycVQWGn6tPQqJRoF9pkqwXh8kZ78t9LGypx)

brand=`ESTEL PROFESSIONAL` · line=`DELUXE` · shade=`None` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": "ESTEL PROFESSIONAL",
  "line": "DELUXE",
  "volume": {
    "value": 1000,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": null,
  "is_set": false
}
```

### 41. `p01442`

**Мыло хозяйственное с глицерином 72%, в плёнке, 140 г**

[карточка товара](https://www.wildberries.ru/catalog/559498542/detail.aspx)

ловушки: `dirty_unit`

brand=`None` · line=`None` · shade=`None` · category=`мыло`

```json
{
  "category": "мыло",
  "brand": null,
  "line": null,
  "volume": {
    "value": 140,
    "unit": "g"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 42. `p01238`

**Лавандовый край, Масло кокосовое нерафинированное**

[карточка товара](https://www.ozon.ru/products/522476748/?at=J8tgEP4vxs9vPJorHlG3v8niOvA6ElFEP2zNH3OGoWw)

brand=`Лавандовый край` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Лавандовый край",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "масло",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 43. `p01569`

**Освежитель воздуха Chirton "Китайская магнолия" сухое распыление для дома, туалета и ванны, 300 мл**

[карточка товара](https://www.ozon.ru/product/osvezhitel-vozduha-chirton-kitayskaya-magnoliya-suhoe-raspylenie-dlya-doma-tualeta-i-vanny-300-ml-523968889/?asb=ZYsab8K4qMoD6iOF4xypGu2X4gwt6W%252FQxAguaySaX34%253D&asb2=_jx3sbRa1sBC_YU9Ni8ziokPms3qPWy3C49RW7-IkQ7QjvqpFPiEta9hVskztg-e_ipxqAeJe2G2WZGnn9EMtw&avtc=1&avte=2&avts=1706994784)

ловушки: `quoted`

brand=`Chirton` · line=`None` · shade=`Китайская магнолия` · category=`освежитель_аромат`

```json
{
  "category": "освежитель_аромат",
  "brand": "Chirton",
  "line": null,
  "volume": {
    "value": 300,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "спрей",
  "purpose": [],
  "shade": "Китайская магнолия",
  "is_set": false
}
```

### 44. `p00720`

**Восстанавливающая маска для волос профессиональная.**

[карточка товара](https://www.wildberries.ru/catalog/540778933/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "восстановление"
  ],
  "shade": null,
  "is_set": false
}
```

### 45. `p00730`

**временная краска для волос окрашивание для подростка**

[карточка товара](https://www.wildberries.ru/catalog/882146329/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": null,
  "is_set": false
}
```

### 46. `p01038`

**Косметика для девочек Зефирка &#34;десертная&#34; серия. Блеск для губ детский &#34;Черничное суфле&#34;, 8гр**

[карточка товара](https://www.ozon.ru/product/kosmetika-dlya-devochek-zefirka-34-desertnaya-34-seriya-blesk-dlya-gub-detskiy-34-chernichnoe-sufle-523052639/?asb=J58KZl0R6Yv9VAxmnSWvXlNtOL2%252BHgwRWIjDk99D36g%253D&asb2=qiLvPsAt0ghVMRxG7tOMqpLGDSiKynD9YcRBC-CuG8_llL95wDOmkUIGC2_Fwn2x&avtc=1&avte=2&avts=1672188198)

ловушки: `dirty_unit`

brand=`Зефирка` · line=`десертная` · shade=`Черничное суфле` · category=`средство_для_губ`

```json
{
  "category": "средство_для_губ",
  "brand": "Зефирка",
  "line": "десертная",
  "volume": {
    "value": 8,
    "unit": "g"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "детское"
  ],
  "shade": "Черничное суфле",
  "is_set": false
}
```

### 47. `p01415`

**мыло для бровей с ароматом персика**

[карточка товара](https://www.wildberries.ru/catalog/532666374/detail.aspx?root=540267937)

brand=`None` · line=`None` · shade=`None` · category=`мыло`

```json
{
  "category": "мыло",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "твердое",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 48. `p01109`

**Краска для волос стойкая 7.00 средне-русый для седых 100 мл + оксид 6% 100 мл**

[карточка товара](https://www.wildberries.ru/catalog/559242933/detail.aspx)

ловушки: `shade_code`, `set_like`

brand=`None` · line=`None` · shade=`7.00 средне-русый` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": "7.00 средне-русый",
  "is_set": true
}
```

### 49. `p01451`

**Набор "Жаркий Таити"**

[карточка товара](https://www.wildberries.ru/catalog/560040456/detail.aspx)

ловушки: `quoted`, `set_like`

brand=`None` · line=`Жаркий Таити` · shade=`None` · category=`парфюмерия`

```json
{
  "category": "парфюмерия",
  "brand": null,
  "line": "Жаркий Таити",
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 50. `p00570`

**Адвент календарь с детскими татуировками18 шт "Мстители" Марвел**

[карточка товара](https://www.ozon.ru/product/advent-kalendar-s-detskimi-tatuirovkami18-sht-mstiteli-marvel-522891419/?asb=BiP49YWuelISchdRMPrhnuvUp1BIqMw6B4slgWVY6UtPBTqgFI%252FruQD0E6hOIPZH&asb2=HJI3k-7s8jLVw5jTSBlRG1yeICkwvrMpANInlc054Gshk4P05JIPxIDHPD_wWDcf86c6lbO5CTD5G0b9lo0-LymW7jnTeP6NVqqoNfdQig9gWheQOBSKzpP0YFpvmucA6W_FUQAbMnkR6v4hjbRLNQ&avtc=1&avte=2&avts=1669333317)

ловушки: `pack`, `quoted`

brand=`Марвел` · line=`None` · shade=`Мстители` · category=`прочее`

```json
{
  "category": "прочее",
  "brand": "Марвел",
  "line": null,
  "volume": null,
  "pack_count": 18,
  "form": null,
  "purpose": [],
  "shade": "Мстители",
  "is_set": true
}
```

### 51. `p02339`

**Шампунь детский витаминный 200 мл**

[карточка товара](https://www.wildberries.ru/catalog/559184750/detail.aspx?root=572272561)

brand=`None` · line=`None` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": null,
  "line": null,
  "volume": {
    "value": 200,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "детское"
  ],
  "shade": null,
  "is_set": false
}
```

### 52. `p00666`

**Блеск для губ розовый нежный**

[карточка товара](https://www.wildberries.ru/catalog/884469082/detail.aspx)

brand=`None` · line=`None` · shade=`розовый нежный` · category=`средство_для_губ`

```json
{
  "category": "средство_для_губ",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": "розовый нежный",
  "is_set": false
}
```

### 53. `p01883`

**Скраб для тела ореховый Народные рецепты жиросжигающий**

[карточка товара](https://www.wildberries.ru/catalog/559970172/detail.aspx)

brand=`Народные рецепты` · line=`None` · shade=`ореховый` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Народные рецепты",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": "ореховый",
  "is_set": false
}
```

### 54. `p01732`

**/ Порошок для мытья посуды в посудомоечной машине Somat Classic,3кг**

[карточка товара](https://www.ozon.ru/product/poroshok-dlya-mytya-posudy-v-posudomoechnoy-mashine-somat-classic-3kg-523626257/?asb=yQKdOzr6hEmKVR44MjNT24LICy3CiGlCg50gAwThxGXBqnYj4xKKQEhgxGJnd1Ec&asb2=q-P6VQSN8O6j1nIKDR2GNBfx_wj7cKZPQ68FOwCufbIHUfYg-EHGG4Yt3XX-KS111n-rX8v7ixWi_NKSolrITiYJtJ5EoP4_HO21b3JOrI8W_R1hZ-gf6J3sjschU14Orn8F2A4ghNaEgeXxMAayUOacg9ucALYJcW8mTJl1aOM&avtc=1&avte=2&avts=1680996348)

ловушки: `dirty_unit`

brand=`Somat` · line=`Classic` · shade=`None` · category=`средство_для_посуды`

```json
{
  "category": "средство_для_посуды",
  "brand": "Somat",
  "line": "Classic",
  "volume": {
    "value": 3000,
    "unit": "g"
  },
  "pack_count": 1,
  "form": "порошок",
  "purpose": [
    "очищение"
  ],
  "shade": null,
  "is_set": false
}
```

### 55. `p02078`

**Стик воск для укладки вьющися волос прозрачный**

[карточка товара](https://www.wildberries.ru/catalog/541476181/detail.aspx?root=552348725)

brand=`None` · line=`None` · shade=`прозрачный` · category=`стайлинг`

```json
{
  "category": "стайлинг",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": "прозрачный",
  "is_set": false
}
```

### 56. `p02436`

**Экспресс маска для лица увлажняющая**

[карточка товара](https://www.wildberries.ru/catalog/559323897/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`маска_для_лица`

```json
{
  "category": "маска_для_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "увлажнение"
  ],
  "shade": null,
  "is_set": false
}
```

### 57. `p00748`

**Гель Domestos Зеленая свежесть с активными пробиотиками универсальный 750 мл, набор: 2 штуки**

[карточка товара](https://www.ozon.ru/products/3119713185/?at=VvtzoJY6NTrKr46vTOAVzEMswV6nmJhRL2LD6hAkJZDQ)

ловушки: `pack`, `set_like`, `multi_volume`

brand=`Domestos` · line=`Зеленая свежесть` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": "Domestos",
  "line": "Зеленая свежесть",
  "volume": {
    "value": 750,
    "unit": "ml"
  },
  "pack_count": 2,
  "form": "гель",
  "purpose": [
    "очищение"
  ],
  "shade": null,
  "is_set": false
}
```

### 58. `p02156`

**Таблетка инсектицидная Искра Двойной Эффект 10 г**

[карточка товара](https://www.ozon.ru/product/tabletka-insektitsidnaya-iskra-dvoynoy-effekt-10-g-522957520/)

ловушки: `dirty_unit`

brand=`Искра` · line=`Двойной Эффект` · shade=`None` · category=`дезинсекция`

```json
{
  "category": "дезинсекция",
  "brand": "Искра",
  "line": "Двойной Эффект",
  "volume": {
    "value": 10,
    "unit": "g"
  },
  "pack_count": 1,
  "form": "таблетки",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 59. `p01683`

**Подарочный набор для девочки My Little Pony "Для самой волшебной", расческа и зеркало**

[карточка товара](https://www.ozon.ru/product/podarochnyy-nabor-dlya-devochki-my-little-pony-dlya-samoy-volshebnoy-rascheska-i-zerkalo-523727441/?asb=TlVssUE%252FoNqzz9TPAXyCXKiSAm7JVwAbNV%252F40YhpgwA%253D&asb2=75bEy55gsqg6rYosyagADZZ-0n6A90f6XoHjutlPNXIBbTXq3DQIPj6jjR5zutTD&avtc=1&avte=2&avts=1675571195)

ловушки: `quoted`, `set_like`

brand=`My Little Pony` · line=`None` · shade=`None` · category=`аксессуары`

```json
{
  "category": "аксессуары",
  "brand": "My Little Pony",
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 60. `p01692`

**Подарочный набор косметики Red Space: пена для бритья, 200 мл + лосьон после бритья, 100 мл**

[карточка товара](https://www.ozon.ru/products/3119834738/?at=z6tOqkwqpcOjROcGEzrBSwOl0j6iN60kLqck6gg2)

ловушки: `set_like`, `multi_volume`

brand=`Red Space` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Red Space",
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 61. `p00990`

**Китайская чудо мазь для лечения угрей прыщей акне рубцов**

[карточка товара](https://www.wildberries.ru/catalog/528536066/detail.aspx?root=535287364)

brand=`None` · line=`None` · shade=`None` · category=`средство_от_кожных_проблем`

```json
{
  "category": "средство_от_кожных_проблем",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "против_акне"
  ],
  "shade": null,
  "is_set": false
}
```

### 62. `p01854`

**Салфетки ЛЕНТА целлюлозные 15x15см Арт. 74648, 5шт**

[карточка товара](https://www.utkonos.ru/item/512490/salfetki-lenta-15x15--celljuloznye-5sht?offerId=512490)

ловушки: `pack`

brand=`ЛЕНТА` · line=`None` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": "ЛЕНТА",
  "line": null,
  "volume": null,
  "pack_count": 5,
  "form": "салфетки",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 63. `p01645`

**Пенка для умывания лица увлажняющая очищающая с щеточкой, !**

[карточка товара](https://www.wildberries.ru/catalog/882563097/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`очищение_лица`

```json
{
  "category": "очищение_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "пенка",
  "purpose": [
    "увлажнение",
    "очищение"
  ],
  "shade": null,
  "is_set": false
}
```

### 64. `p00607`

**Астра-Н Подставка спиралей от комаров, 1 шт.**

[карточка товара](https://www.ozon.ru/product/astra-n-podstavka-spiraley-ot-komarov-1-sht-1143929883/?asb=FmUv12quaA2qPYGYmwh9rsPH7XQMmcEbms0MjUTfPNo%253D&asb2=oJ7l3QbU0JXAJIRP_Wwzd7VtRDkSk0PDJhukim7pLz-Cf9MWEPzNawVx4LyxPUfr4SMaPTCggoHhILS_hW6UgA&avtc=1&avte=2&avts=1715063669)

ловушки: `pack`

brand=`Астра-Н` · line=`None` · shade=`None` · category=`дезинсекция`

```json
{
  "category": "дезинсекция",
  "brand": "Астра-Н",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 65. `p00342`

**Lion Порошковое средство для мытья посуды в посудомойке 650 гр с цитрусовым ароматом, Япония**

[карточка товара](https://www.ozon.ru/products/170710487/?at=MZtv4lGnMC4X26lXSzLqmPGUqOK7KyHqXgW5lT6OgQKv)

ловушки: `dirty_unit`

brand=`Lion` · line=`None` · shade=`None` · category=`средство_для_посуды`

```json
{
  "category": "средство_для_посуды",
  "brand": "Lion",
  "line": null,
  "volume": {
    "value": 650,
    "unit": "g"
  },
  "pack_count": 1,
  "form": "порошок",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 66. `p01612`

**Очищающая антивозрастная сыворотка для кожи головы Scalp Clarifying Cleansing Serum, 100 мл**

[карточка товара](https://www.wildberries.ru/catalog/559237714/detail.aspx?root=572351323)

brand=`None` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": null,
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "сыворотка",
  "purpose": [
    "очищение",
    "антивозрастное"
  ],
  "shade": null,
  "is_set": false
}
```

### 67. `p01850`

**Салфетки для стирки от окрашивания, ловушка цвета**

[карточка товара](https://www.wildberries.ru/catalog/558879103/detail.aspx?root=571921802)

brand=`None` · line=`None` · shade=`None` · category=`средство_для_стирки`

```json
{
  "category": "средство_для_стирки",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "салфетки",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 68. `p01252`

**Лак для ногтей Pretty розовый нюдовый минилак 5мл**

[карточка товара](https://www.ozon.ru/product/lak-dlya-nogtey-pretty-522640390/?asb=zHICALgER0LUJz94u70LECTxuRX3JVyz27pBeePJIEQ%253D&asb2=4ov98eKmiDHSPBfDeN2Kb11g8BrdBWp8BACDMXltidzx28v9Glg9oa5hBbPzeJ_n&avtc=1&avte=2&avts=1682496548)

brand=`Pretty` · line=`None` · shade=`розовый нюдовый` · category=`декоративная_косметика`

```json
{
  "category": "декоративная_косметика",
  "brand": "Pretty",
  "line": null,
  "volume": {
    "value": 5,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": "розовый нюдовый",
  "is_set": false
}
```

### 69. `p00895`

**Доместос Универсальное чистящее средство Ультра блеск, 1000 мл, 2 шт**

[карточка товара](https://www.ozon.ru/products/3119764392/?at=GRt2X79x4uyP73P9slGAkpGF4jQDAwumQx9Y9SYGKowV)

ловушки: `pack`, `multi_volume`

brand=`Доместос` · line=`Ультра блеск` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": "Доместос",
  "line": "Ультра блеск",
  "volume": {
    "value": 1000,
    "unit": "ml"
  },
  "pack_count": 2,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 70. `p00341`

**LION Корейский стиральный порошок / Порошок стиральный Beat для ручной и автоматической стирки, 4 кг**

[карточка товара](https://www.ozon.ru/product/lion-koreyskiy-poroshok-dlya-ruchnoy-i-avtomaticheskoy-stirki-s-mernoy-lozhkoy-korobka-4-kg-172428939/?asb2=AWU7QQvjwOuzRiIKIclHIiBLhtvXNcXOw88hPd8MBVcQstT9Nay_gjBIdUZ9IHRvIlH_BVK2jHeKMujXcJ3CxA&avtc=1&avte=2&avts=1706780410)

ловушки: `dirty_unit`

brand=`LION` · line=`None` · shade=`None` · category=`средство_для_стирки`

```json
{
  "category": "средство_для_стирки",
  "brand": "LION",
  "line": null,
  "volume": {
    "value": 4000,
    "unit": "g"
  },
  "pack_count": 1,
  "form": "порошок",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 71. `p00645`

**Бальзам после загара Med Пантенол 7%, Гиалуроновая кислота**

[карточка товара](https://www.wildberries.ru/catalog/546303634/detail.aspx)

brand=`Med` · line=`None` · shade=`None` · category=`уход_за_телом`

```json
{
  "category": "уход_за_телом",
  "brand": "Med",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "бальзам",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 72. `p00678`

**Блок Доместос Power 5 свежесть лайма и океана для туалета 5 шт, набор: 3 штуки**

[карточка товара](https://www.ozon.ru/products/3119761662/?at=08tYwxK1xcwy072lupnXqlOcOq50GGH4wJX7YFM6ox8Y)

ловушки: `pack`, `set_like`, `multi_volume`

brand=`Доместос` · line=`Power` · shade=`None` · category=`освежитель_аромат`

```json
{
  "category": "освежитель_аромат",
  "brand": "Доместос",
  "line": "Power",
  "volume": null,
  "pack_count": 3,
  "form": "блок",
  "purpose": [
    "против_запаха"
  ],
  "shade": null,
  "is_set": false
}
```

### 73. `p02180`

**Твердый шампунь для жирных волос с кокосом**

[карточка товара](https://www.wildberries.ru/catalog/886043321/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "твердое",
  "purpose": [
    "против_жира"
  ],
  "shade": null,
  "is_set": false
}
```

### 74. `p01200`

**Крем отбеливающий для лица и тела**

[карточка товара](https://www.wildberries.ru/catalog/550941116/detail.aspx?root=562874685)

brand=`None` · line=`None` · shade=`None` · category=`средство_от_кожных_проблем`

```json
{
  "category": "средство_от_кожных_проблем",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "крем",
  "purpose": [
    "осветление"
  ],
  "shade": null,
  "is_set": false
}
```

### 75. `p00387`

**NASHI ARGAN AFTER SUN HYDRATING Shampoo Шампунь увлаж 200 мл**

[карточка товара](https://www.wildberries.ru/catalog/533204497/detail.aspx?root=541405154)

ловушки: `truncated`

brand=`NASHI` · line=`ARGAN AFTER SUN HYDRATING` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": "NASHI",
  "line": "ARGAN AFTER SUN HYDRATING",
  "volume": {
    "value": 200,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "увлажнение"
  ],
  "shade": null,
  "is_set": false
}
```

### 76. `p00112`

**BOTANIC THE RAPY маска - молочко для волос**

[карточка товара](https://www.wildberries.ru/catalog/882517137/detail.aspx)

brand=`BOTANIC THE RAPY` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": "BOTANIC THE RAPY",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 77. `p01979`

**Спрей Ультра гигиена универсальный 500мл 2 штуки**

[карточка товара](https://www.wildberries.ru/catalog/559498788/detail.aspx?root=572555295)

ловушки: `pack`, `multi_volume`

brand=`None` · line=`Ультра гигиена` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": null,
  "line": "Ультра гигиена",
  "volume": {
    "value": 500,
    "unit": "ml"
  },
  "pack_count": 2,
  "form": "спрей",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 78. `p00930`

**Жидкий лак-спрей для волос SILHOUETTE 200 мл**

[карточка товара](https://www.wildberries.ru/catalog/881794194/detail.aspx)

brand=`SILHOUETTE` · line=`None` · shade=`None` · category=`стайлинг`

```json
{
  "category": "стайлинг",
  "brand": "SILHOUETTE",
  "line": null,
  "volume": {
    "value": 200,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "спрей",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 79. `p01504`

**Набор Система 1 для натуральных волос от истончения, 700 мл**

[карточка товара](https://www.wildberries.ru/catalog/533515844/detail.aspx?root=541699385)

ловушки: `set_like`, `truncated`

brand=`None` · line=`Система 1` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": null,
  "line": "Система 1",
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "против_выпадения"
  ],
  "shade": null,
  "is_set": true
}
```

### 80. `p01773`

**Расческа для мелирования профессиональная набор 3 шт**

[карточка товара](https://www.wildberries.ru/catalog/559874878/detail.aspx?root=242414306)

ловушки: `pack`, `set_like`

brand=`None` · line=`None` · shade=`None` · category=`аксессуары`

```json
{
  "category": "аксессуары",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 3,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 81. `p00772`

**Гель для душа мужской парфюмированный**

[карточка товара](https://www.wildberries.ru/catalog/885728749/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`гель_для_душа`

```json
{
  "category": "гель_для_душа",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "гель",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 82. `p00680`

**Блок Доместос Сила 5 в 1 Свежесть лайма и ледяная магнолия для унитаза 5 шт, набор: 3 штуки**

[карточка товара](https://www.ozon.ru/products/3119761917/?at=J8tgwpO2oI2D1959twKlz3WhA9WX5GhqjPPRXuLkMmoy)

ловушки: `pack`, `set_like`, `multi_volume`

brand=`Доместос` · line=`Сила 5 в 1` · shade=`None` · category=`чистящее_средство`

```json
{
  "category": "чистящее_средство",
  "brand": "Доместос",
  "line": "Сила 5 в 1",
  "volume": null,
  "pack_count": 3,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 83. `p00221`

**Gillette одноразовые мужские бритвы Blue2 Plus, с 2 лезвиями, 5 шт, фиксированная головка**

[карточка товара](https://www.ozon.ru/products/5543603/?at=QktJQgpv8cAk3jQXTXW5JBqC1r1NR6tvv1DopUGggml2)

ловушки: `pack`

brand=`Gillette` · line=`Blue2 Plus` · shade=`None` · category=`аксессуары`

```json
{
  "category": "аксессуары",
  "brand": "Gillette",
  "line": "Blue2 Plus",
  "volume": null,
  "pack_count": 5,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 84. `p01593`

**Оттеночный бальзам для волос NEWTONE 7.44, 60 мл**

[карточка товара](https://www.wildberries.ru/catalog/543449990/detail.aspx)

ловушки: `shade_code`

brand=`NEWTONE` · line=`None` · shade=`7.44` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": "NEWTONE",
  "line": null,
  "volume": {
    "value": 60,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "тонирование"
  ],
  "shade": "7.44",
  "is_set": false
}
```

### 85. `p00488`

**sadoer маска лица от черных точек**

[карточка товара](https://www.wildberries.ru/catalog/552101943/detail.aspx)

brand=`sadoer` · line=`None` · shade=`None` · category=`маска_для_лица`

```json
{
  "category": "маска_для_лица",
  "brand": "sadoer",
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "против_акне"
  ],
  "shade": null,
  "is_set": false
}
```

### 86. `p00376`

**Mommy Care Масло для отпугивания комаров 50 мл, 0+**

[карточка товара](https://www.ozon.ru/product/mommy-care-maslo-dlya-otpugivaniya-komarov-50-ml-0-149735630/?asb=dwuCqjX1vu97D%252FIDKDKqxatJdafjRQin6jKscnCQUsc%253D&asb2=CZLnCsk_ZFZyicB5Ytaz7vhK6uXPzj0Q6T2FDtMflAdDTBTGvabpGS4hxP_QRYBzq-kvBwgFRx8KUzDmRVA3SA&avtc=1&avte=2&avts=1721789593)

ловушки: `set_like`

brand=`Mommy Care` · line=`None` · shade=`None` · category=`прочее`

```json
{
  "category": "прочее",
  "brand": "Mommy Care",
  "line": null,
  "volume": {
    "value": 50,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "масло",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 87. `p01570`

**Освежитель воздуха Chirton "Прохлада раннего утра" сухое распыление для дома, туалета и ванны, 300 мл**

[карточка товара](https://www.ozon.ru/products/523973257/?at=OgtE8lj1Js9EZ0YnI8Lgy16UOJRYWRFB39gguPGqLr8)

ловушки: `quoted`

brand=`Chirton` · line=`Прохлада раннего утра` · shade=`None` · category=`освежитель_аромат`

```json
{
  "category": "освежитель_аромат",
  "brand": "Chirton",
  "line": "Прохлада раннего утра",
  "volume": {
    "value": 300,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "спрей",
  "purpose": [
    "против_запаха"
  ],
  "shade": null,
  "is_set": false
}
```

### 88. `p01345`

**Маски для лица sadoer 20 штук,**

[карточка товара](https://www.wildberries.ru/catalog/546686294/detail.aspx?root=557553411)

ловушки: `pack`

brand=`sadoer` · line=`None` · shade=`None` · category=`маска_для_лица`

```json
{
  "category": "маска_для_лица",
  "brand": "sadoer",
  "line": null,
  "volume": null,
  "pack_count": 20,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 89. `p00807`

**Гель для стирки ЛАСКА Шерсть и шелк, 2л**

[карточка товара](https://www.utkonos.ru/item/115037/sredstvo-dstirki-laska-sherst-i-shelk-2l?offerId=115037)

ловушки: `dirty_unit`

brand=`ЛАСКА` · line=`Шерсть и шелк` · shade=`None` · category=`средство_для_стирки`

```json
{
  "category": "средство_для_стирки",
  "brand": "ЛАСКА",
  "line": "Шерсть и шелк",
  "volume": {
    "value": 2000,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "гель",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 90. `p01349`

**Маски для лица полиэтиленовые косметические 100 штук**

[карточка товара](https://www.wildberries.ru/catalog/530981081/detail.aspx)

ловушки: `pack`

brand=`None` · line=`None` · shade=`None` · category=`маска_для_лица`

```json
{
  "category": "маска_для_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 100,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 91. `p01505`

**Набор трафаретов для создания блеск тату**

[карточка товара](https://www.ozon.ru/product/nabor-trafaretov-dlya-sozdaniya-blesk-tatu-523507669/?asb=j%252FJHOlN5XEhHtk6UmWuNfi725nuD0cc9cq1oFNznQ%252BM%253D&asb2=1-v-3YphNf9tgQPK0wdD1GYGtBTZZOqjeL7CTO7lZDP6m-hlOIwbenN3YuBhz8sY&avtc=1&avte=2&avts=1677456007)

ловушки: `set_like`

brand=`None` · line=`None` · shade=`None` · category=`расходники_бьюти`

```json
{
  "category": "расходники_бьюти",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 92. `p02248`

**фитокосметик bio cosmetolog professional спрей со 190мл 3 шт**

[карточка товара](https://www.wildberries.ru/catalog/552686078/detail.aspx)

ловушки: `pack`, `multi_volume`, `truncated`

brand=`bio cosmetolog professional` · line=`None` · shade=`None` · category=`освежитель_аромат`

```json
{
  "category": "освежитель_аромат",
  "brand": "bio cosmetolog professional",
  "line": null,
  "volume": {
    "value": 190,
    "unit": "ml"
  },
  "pack_count": 3,
  "form": "спрей",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 93. `p00939`

**Зефирка Glowgirl. Набор подарочный декоративной косметики для девочек "Сверкающий бирюзовый".**

[карточка товара](https://www.ozon.ru/product/zefirka-glowgirl-nabor-podarochnyy-dekorativnoy-kosmetiki-dlya-devochek-sverkayushchiy-biryuzovyy-523045121/?asb=nO9RP6RyM0BTSwCtBAHU44M2sUx6d5E9ZzpywOCP4rQ%253D&asb2=wPXoxo3L2D204BjdNBOnukB5ggh_w8-3j7VV8LDpwXX5BZS4ANlmI8dFhW_SPQNH&avtc=1&avte=2&avts=1677628019)

ловушки: `quoted`, `set_like`

brand=`Зефирка` · line=`Glowgirl` · shade=`Сверкающий бирюзовый` · category=`декоративная_косметика`

```json
{
  "category": "декоративная_косметика",
  "brand": "Зефирка",
  "line": "Glowgirl",
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": "Сверкающий бирюзовый",
  "is_set": true
}
```

### 94. `p00083`

**Bayer Сыворотка для волос, 100 мл**

[карточка товара](https://www.ozon.ru/product/bayer-syvorotka-dlya-volos-100-ml-3119974525/?at=28t0XmwP3uEmq9krC4GEQvCP38mYwhE11zQ7iAn2klW)

brand=`Bayer` · line=`None` · shade=`None` · category=`уход_для_волос`

```json
{
  "category": "уход_для_волос",
  "brand": "Bayer",
  "line": null,
  "volume": {
    "value": 100,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": "сыворотка",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 95. `p01387`

**Мицеллярная вода 4в1 для снятия макияжа**

[карточка товара](https://www.wildberries.ru/catalog/547352055/detail.aspx?root=558344980)

ловушки: `set_like`

brand=`None` · line=`None` · shade=`None` · category=`очищение_лица`

```json
{
  "category": "очищение_лица",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "очищение"
  ],
  "shade": null,
  "is_set": false
}
```

### 96. `p01067`

**Краска для волос 7/3 средний блондин золотистый**

[карточка товара](https://www.wildberries.ru/catalog/884382989/detail.aspx)

ловушки: `shade_code`

brand=`None` · line=`None` · shade=`7/3 средний блондин золотистый` · category=`краска_для_волос`

```json
{
  "category": "краска_для_волос",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [
    "окрашивание"
  ],
  "shade": "7/3 средний блондин золотистый",
  "is_set": false
}
```

### 97. `p01583`

**Отбеливатель для одежды**

[карточка товара](https://www.wildberries.ru/catalog/884938654/detail.aspx)

brand=`None` · line=`None` · shade=`None` · category=`средство_для_стирки`

```json
{
  "category": "средство_для_стирки",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

### 98. `p01454`

**Набор Kevin.Murphy HIGH EXPECTATIONS, шампунь + кондиционер + пудра для объема**

[карточка товара](https://www.ozon.ru/products/3119710465/?at=28t0zpRWPiZr3zZ8hLn832Atk1z57s32B8ZNcWL3LWR)

ловушки: `set_like`

brand=`Kevin.Murphy` · line=`HIGH EXPECTATIONS` · shade=`None` · category=`шампунь`

```json
{
  "category": "шампунь",
  "brand": "Kevin.Murphy",
  "line": "HIGH EXPECTATIONS",
  "volume": null,
  "pack_count": null,
  "form": null,
  "purpose": [],
  "shade": null,
  "is_set": true
}
```

### 99. `p00173`

**Ecolatier МылодлятелаиволосПитание&ВосстановлениеOrganicCoconut 350 мл 1 шт**

[карточка товара](https://www.wildberries.ru/catalog/533233319/detail.aspx?root=541433387)

ловушки: `pack`, `multi_volume`

brand=`Ecolatier` · line=`OrganicCoconut` · shade=`None` · category=`мыло`

```json
{
  "category": "мыло",
  "brand": "Ecolatier",
  "line": "OrganicCoconut",
  "volume": {
    "value": 350,
    "unit": "ml"
  },
  "pack_count": 1,
  "form": null,
  "purpose": [
    "питание",
    "восстановление"
  ],
  "shade": null,
  "is_set": false
}
```

### 100. `p01197`

**Крем от псориаза Psoriasis Cream**

[карточка товара](https://www.wildberries.ru/catalog/882632510/detail.aspx?root=1017153035)

brand=`None` · line=`None` · shade=`None` · category=`средство_от_кожных_проблем`

```json
{
  "category": "средство_от_кожных_проблем",
  "brand": null,
  "line": null,
  "volume": null,
  "pack_count": 1,
  "form": "крем",
  "purpose": [],
  "shade": null,
  "is_set": false
}
```

# เปรียบเทียบ Layer-based และ Hybrid Feature-based

ทั้งสองแบบใช้ `src/marketplace/` เป็น Python package และแยก config, scripts, migrations
กับ tests ไว้ที่ root เหมือนกัน ความต่างคือจะจัดตาม layer หรือ feature ก่อน

ในเอกสารนี้ **Hybrid Feature-based** หมายถึงแยก feature ก่อน แล้วแบ่ง layer ภายใน feature
ซึ่งเป็นโครงสร้างที่โปรเจกต์นี้ใช้อยู่

## 1. Layer-based: จัดตามหน้าที่ทางเทคนิคก่อน

ตัวอย่างด้านล่างเป็นทางเลือกสำหรับเปรียบเทียบ:

```text
src/marketplace/
├── main.py
├── domain/
│   ├── listings/
│   └── coupons/
├── application/
│   ├── listings/
│   └── coupons/
├── infrastructure/
│   ├── database.py
│   ├── listings/
│   └── coupons/
└── presentation/
    ├── listings/
    └── coupons/
```

การจัดกลุ่มเริ่มจากคำถามว่าโค้ดนี้เป็น domain, use case, adapter หรือ HTTP endpoint
แล้วจึงแยกตาม feature ภายในแต่ละ layer

เหมาะเมื่อทีมต้องการดูโค้ดประเภทเดียวกันรวมอยู่ในพื้นที่เดียว เช่น ตรวจ repository ทั้งหมด
แต่การเปลี่ยนหนึ่ง feature อาจต้องเปิดหลายโฟลเดอร์ที่อยู่คนละส่วนของ package

## 2. Hybrid Feature-based: จัดตามความสามารถของระบบก่อน

โครงสร้างปัจจุบัน:

```text
src/marketplace/
├── main.py
├── settings.py
├── logging_config.py
├── worker.py
├── indexer.py
├── listings/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── presentation/
├── coupons/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── presentation/
└── infrastructure/
    ├── database.py
    └── blockchain/
        └── abi/
```

การจัดกลุ่มเริ่มจากคำถามว่าโค้ดนี้เป็นของ listings หรือ coupons แล้วจึงแบ่ง layer
การแก้หนึ่ง feature จึงอยู่ใต้โฟลเดอร์เดียวกัน ส่วนเครื่องมือที่ใช้ร่วมกันอยู่ระดับ package

ปัจจุบัน `listings` มี implementation สำหรับอ่านรายการขาย ส่วน `coupons` มีเฉพาะโครง
Python packages ยังไม่มี use case, ORM model หรือ HTTP endpoint สำหรับสร้างหรือ mint คูปอง

## เปรียบเทียบการใช้งาน

| เรื่อง | Layer-based | Hybrid Feature-based |
| --- | --- | --- |
| กลุ่มหลัก | ประเภทของโค้ด เช่น domain หรือ application | ความสามารถของระบบ เช่น listings หรือ coupons |
| ดูโค้ดประเภทเดียวกัน | อยู่ใต้ layer เดียวกัน | กระจายตาม feature |
| แก้หนึ่ง feature | เปิดหลาย layer ที่ระดับ package | เปิด layers ภายใน feature เดียว |
| เพิ่ม feature | เพิ่มโฟลเดอร์ย่อยในแต่ละ layer ที่ต้องใช้ | เพิ่ม feature แล้วสร้าง layers ตามที่มีงานจริง |
| แบ่งความรับผิดชอบในทีม | กำหนดเจ้าของหลาย path ต่อ feature | กำหนดเจ้าของที่โฟลเดอร์ feature ได้ |
| สิ่งที่ต้องระวัง | แต่ละ layer รวมหลายเรื่องจนหาโค้ดยาก | Features เรียกข้ามกันจนขอบเขตความเป็นเจ้าของไม่ชัด |
| ส่วนกลาง | แยก technical utilities ได้ | แยก technical utilities ได้เหมือนกัน |
| การแยก deploy | ไม่ได้บังคับให้เป็น microservices | ไม่ได้บังคับให้เป็น microservices |

ทั้งสองแบบรักษาหลัก Clean Architecture ได้ การย้ายโฟลเดอร์อย่างเดียวไม่ได้รับประกัน
ว่าทิศทาง dependency ถูกต้อง โปรเจกต์นี้จึงใช้ Import Linter ตรวจด้วย `make check`

## แต่ละ layer เก็บอะไร

| สิ่งที่เขียน | Layer | ตัวอย่างตำแหน่งใน feature |
| --- | --- | --- |
| Entity และข้อมูลธุรกิจ | Domain | `listings/domain/entities.py` |
| Use case และการประสานขั้นตอน | Application | `listings/application/use_cases.py` |
| Interface ที่ use case ต้องใช้ | Application | `listings/application/ports.py` |
| Query DTO และผลลัพธ์ use case | Application | `listings/application/dto.py` |
| ORM model ที่แทนตาราง | Infrastructure | `listings/infrastructure/models.py` |
| SQL, query, join และการบันทึกข้อมูล | Infrastructure | `listings/infrastructure/repository.py` |
| Request/response models และ HTTP validation | Presentation | `listings/presentation/schemas.py` |
| HTTP routes | Presentation | `listings/presentation/router.py` |

Repository เป็นตัวอ่านเขียนข้อมูล ส่วน ORM model เป็นตัวแทนตาราง
Domain model ไม่จำเป็นต้องมีรูปร่างตรงกับตาราง และผล join ไม่จำเป็นต้องเป็น Entity เสมอไป
หากเป็นข้อมูลรวมเพื่อแสดงผล สามารถคืนเป็น Query DTO ของ Application แล้วแปลงเป็น HTTP response
ใน Presentation ได้

ทิศทาง dependency ภายใน feature:

```text
Presentation → Application → Domain
Infrastructure → Application ports / Domain
```

Application ขอข้อมูลผ่าน port ส่วน Infrastructure เป็น implementation ที่เขียน SQL หรือเรียก SDK
`main.py` ประกอบ implementation เข้ากับ use case แล้ว inject ให้ HTTP router

## แยกส่วนกลางกับโค้ดของ feature อย่างไร

ใช้ความเป็นเจ้าของและหน้าที่เป็นเกณฑ์: โค้ดที่ต้องรู้เรื่อง listings ให้ listings ดูแล
โค้ดที่ต้องรู้เรื่อง coupons ให้ coupons ดูแล ส่วนกลไกทางเทคนิคที่ใช้ร่วมกันค่อยแยกออกมา

| ของส่วนกลาง | ของ feature |
| --- | --- |
| Database Engine, connection pool และ Session factory | ORM models และ queries ของ feature |
| Config loader | ค่าหรือ policy ที่เป็นของ use case นั้น |
| JSON logging และ secret redaction | ข้อมูลเหตุการณ์ที่ feature ต้องการบันทึก |
| ABI reference assets ที่หลาย feature ใช้ | Adapter ที่เรียกฟังก์ชัน contract สำหรับ feature นั้น |

ตัวอย่างการใช้ database เดียวกัน:

```text
listings/infrastructure/repository.py ─┐
                                     ├─ ใช้ Session จาก infrastructure/database.py
coupons/infrastructure/repository.py ──┘
```

ฝั่ง coupons ในตัวอย่างนี้เป็นตำแหน่งสำหรับ implementation ในอนาคต
ไม่จำเป็นต้องมี database หรือ connection pool แยกต่อ feature แต่แต่ละ feature ยังเป็นเจ้าของ
models และ queries ของตัวเอง

แนวทางสำหรับโปรเจกต์นี้:

- เริ่มโค้ดธุรกิจใน feature ที่เป็นเจ้าของก่อน ย้ายเป็นส่วนกลางเมื่อมีการใช้ร่วมจริงและหน้าที่เดียวกัน
- การใช้ database เดียวกันไม่ใช่เหตุผลให้รวม ORM models ทุก feature ไว้ส่วนกลาง
- Shared infrastructure ไม่ import features กลับเข้ามา จุดประกอบอย่าง `main.py` และ
  `migrations/env.py` เป็นผู้เชื่อม feature models กับเครื่องมือส่วนกลาง
- หาก feature ต้องใช้ข้อมูลของอีก feature ให้กำหนด interface หรือ event ที่เปิดให้ใช้
  แทนการเข้าไปแก้ตารางของอีก feature โดยตรง
- Config loader ใช้ร่วมกันได้ แต่ config และสิทธิ์ยังแยกตาม process เช่น API ไม่โหลด KMS secrets
  ของ worker

## ขอบเขตสำหรับระบบ marketplace นี้

เลือก Hybrid Feature-based เพื่อเก็บโค้ดของ listings และ coupons แยกกัน โดยใช้ shared database
infrastructure ร่วมกัน กฎซื้อขาย, burn และ escrow settlement ยังคงอยู่ใน smart contract
backend ดูแลเฉพาะความรับผิดชอบของตนตาม use cases ที่ยืนยันแล้ว

อ่าน [architecture](architecture.md) สำหรับโครงสร้างและขอบเขตปัจจุบัน และ
[contract validation](contract-validation.md) ก่อนเพิ่ม transaction use case

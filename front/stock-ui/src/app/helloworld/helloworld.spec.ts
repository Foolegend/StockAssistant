import { ComponentFixture, TestBed } from '@angular/core/testing';

import { Helloworld } from './helloworld';

describe('Helloworld', () => {
  let component: Helloworld;
  let fixture: ComponentFixture<Helloworld>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Helloworld]
    })
    .compileComponents();

    fixture = TestBed.createComponent(Helloworld);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});

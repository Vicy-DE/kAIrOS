#!/usr/bin/env python3
"""Stand-in for a generated pygame game (F2/F5): a real X window on $DISPLAY with a
bouncing square; title "kairos-game: stub". Quits on Escape / window close / SIGTERM."""
import os
import pygame

pygame.init()
screen = pygame.display.set_mode((400, 300))
pygame.display.set_caption("kairos-game: stub")
clock = pygame.time.Clock()
x, y, vx, vy = 40, 40, 3, 2
running = True
while running:
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
            running = False
    x += vx; y += vy
    if x < 0 or x > 360: vx = -vx
    if y < 0 or y > 260: vy = -vy
    screen.fill((20, 20, 40))
    pygame.draw.rect(screen, (255, 200, 60), (x, y, 40, 40))
    pygame.display.flip()
    clock.tick(60)
pygame.quit()

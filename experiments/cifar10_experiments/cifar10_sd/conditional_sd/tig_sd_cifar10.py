import os
from diffusers import StableDiffusionPipeline
from diffusers.schedulers import DPMSolverMultistepScheduler
from tgate import TgateSDDeepCacheLoader
import  re
import cv2
import random
import numpy as np
import torch
#import wandb
from torch import autocast
from PIL import Image
from torchvision import transforms
from collections import Counter
from cifar10_classifier.model import VGGNet
#run =wandb.init(project="sinvadtestfitness")
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":16:8"
torch.backends.cudnn.benchmark = False
generator = torch.Generator(device = 'cuda')
torch.use_deterministic_algorithms(True)
def process_image(image):
    """
    Resize a 3-channel RGB PIL Image to 32x32 pixels and convert it to a PyTorch tensor.

    Parameters:
    - image (PIL.Image): The input RGB image.

    Returns:
    - tensor (torch.Tensor): The processed image as a PyTorch tensor.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("The provided image needs to be a PIL Image.")

    # Convert PIL Image to numpy array (RGB)
    img_np = np.array(image)

    # Resize the image to 32x32 pixels if it's not already that size
    if img_np.shape[0] != 32 or img_np.shape[1] != 32:
        resized_image = cv2.resize(img_np, (32, 32), interpolation=cv2.INTER_NEAREST)
    else:
        resized_image = img_np

    # Convert the numpy array back to PIL Image (to use torchvision transforms)
    img_pil = Image.fromarray(resized_image)

    # Convert PIL Image to PyTorch Tensor
    transform = transforms.ToTensor()
    tensor = transform(img_pil)

    return tensor
def calculate_fitness(logit, label):

    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness
classifier = VGGNet().to(device)
# Load pretrained model
classifier.load_state_dict(
    torch.load(
        "./cifar10_classifier/CIFAR10_cifar10_train.pynet.pth",
        map_location=device,
   )
)
classifier.eval()
transform = transforms.Compose(
    [
        transforms.Resize((32, 32)),
        #transforms.Grayscale(num_output_channels=1),  # This converts to grayscale
        transforms.ToTensor(),
    ]
)

prompts =[ "A photo of A1plane0 cifar10_0","A photo of car1 cifar10_1","A photo of bird2 cifar10_2","A photo of cat3 cifar10_3","A photo of deer4 cifar10_4","A photo of dog5 cifar10_5","A photo of frog6 cifar10_6","A photo of horse7 cifar10_7","A photo of ship8 cifar10_8","A photo of truck9 cifar10_9"]
proj_name = "test"
num_inference_steps = 25
width = 512
height = 512
min_val = -5.53951740264893
max_val = 5.377610206604
init_perturbation = 0.00218342552185059
perturbation_size = 0.00109171276092529
best_left = 10
gen_num = 250
pop_size = 25
fitness_scores = []
all_img_lst = []
imgs_to_samp = 100
image_info = []
predicted_labels = []
proj_path = "./cifar10_sd/"+proj_name+"_"
os.makedirs(proj_path, exist_ok=True)
os.makedirs(proj_path+'/perturbresult', exist_ok=True)


print('Creating init image')
base_model_id = "runwayml/stable-diffusion-v1-5"
weights_path = "./cifar10_finetune_lorav1.5-000005.safetensors"

pipe = StableDiffusionPipeline.from_pretrained(
base_model_id,variant="fp16", torch_dtype=torch.float16, safety_checker=None).to(device)
pipe.load_lora_weights(weights_path)
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
pipe.unet.to(device)
pipe.vae.to(device)
pipe.text_encoder.to(device)
pipe = TgateSDDeepCacheLoader(
       pipe,
       cache_interval=3,
       cache_branch_id=0,
).to(device)

seed = 0
saved_images = 0
for n in range(imgs_to_samp):
    seedSelect = seed+n
    generator = generator.manual_seed(seedSelect)
    original_lv = torch.randn((1, pipe.unet.config.in_channels, height // 8, width // 8),generator = generator, device=device).to(torch.float16)

    # Generate Prompt and expected label
    randprompt = random.choice(prompts)
    expected_label = int(re.search(r"cifar10_(\d+)", randprompt).group(1))  # Extract the number following 'HouseNo'
    with torch.inference_mode():
        init_img = pipe.tgate(prompt = randprompt,guidance_scale= 3.5,gate_step =10, num_inference_steps= num_inference_steps,latents=original_lv)["images"][0]
    #process the generated image
    tensor_image = process_image(init_img)
    tensor_image = tensor_image.unsqueeze(0).to(device)
    original_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
    original_label = np.argmax(original_logit).item()
    # Check if the predicted label matches the expected label from the prompt
    if original_label == expected_label:
       # Save the image only if the label matches
       init_img_path = os.path.join(proj_path, f'image_{saved_images}_X{original_label}_prompt_{expected_label}.png')
       init_img.save(init_img_path)
       print(f"Image {saved_images} with matching label saved at {init_img_path}")
       saved_images +=1

       init_pop = [
         original_lv + init_perturbation * torch.randn((pipe.unet.config.in_channels, height // 8, width // 8), device=device).to(torch.float16)
         for _ in range(pop_size)
       ]

       best_fitness_score = float('inf')
       best_image_tensor = None
       best_image_index = -1
       now_pop = init_pop
       prev_best = np.inf
       for g_idx in range(gen_num):
          # Flatten all tensors in now_pop into a single tensor for min/max evaluation
           indivs_lv = torch.cat(now_pop, dim=0).view(-1, 4, height // 8, width // 8).to(torch.float16)
           print(indivs_lv.shape)
           
           with torch.inference_mode():                         
                perturb_img = pipe.tgate([randprompt]*(pop_size),guidance_scale =1.4,gate_step = 10, generator= generator, 
                   num_inference_steps=num_inference_steps,
                   latents=indivs_lv,
                )["images"]
           # all_img_lst.append(perturb_img)
           torch.cuda.empty_cache()

           tensor_image2 =torch.stack([process_image(image) for image in perturb_img])
           # tensor_image2 = transform(last_image)
           tensor_image2 = tensor_image2.to(device)
           all_logits = classifier(tensor_image2).detach().cpu().numpy()
           perturb_label1 = np.argmax(all_logits).item()
           print(all_logits.shape)
           print(tensor_image2.shape)
           os.makedirs(os.path.join(proj_path, 'generated_images'), exist_ok=True)
          
           fitness_scores = [
               calculate_fitness(all_logits[k_idx], original_label)
               for k_idx in range(pop_size)
           ]
           print("print fitness",len(fitness_scores))

           # Find the minimum fitness score in the current generation
           current_min_index = np.argmin(fitness_scores)
           current_min_fitness = fitness_scores[current_min_index]
    
           # Update best tracking variables if the current minimum is less than the tracked best
           if current_min_fitness < best_fitness_score:
              best_fitness_score = current_min_fitness
              best_image_tensor  = tensor_image2[current_min_index]  # Ensure a deep copy
              best_image_index = current_min_index
           # Perform selection
           selected_indices = sorted(range(len(fitness_scores)), key=lambda i: fitness_scores[i], reverse=True,
              )[-best_left:]
           now_best = np.min(fitness_scores)
           parent_pop = [now_pop[idx] for idx in selected_indices]
           print(parent_pop[-1].shape)
           print("now_best " + str(now_best) + " average_best " + str(np.mean(fitness_scores)))
          # wandb.log({"ft_score":now_best})
           if now_best < 0:
              break
           elif now_best == prev_best:
               perturbation_size *= 2
           else:
               perturbation_size = init_perturbation
           k_pop = []
           # print("Size of parent_pop:", len(parent_pop))
           # select k-idx for cross_over genes
           for k_idx in range(pop_size - best_left):
               mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
              # print("mom_idx:", mom_idx, "pop_idx:", pop_idx)
               spl_idx = np.random.choice(4, size=1)[0]
               k_gene = torch.cat(
                   [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                   dim=1,
               )  # crossover
               # Mutation
               diffs = (k_gene != original_lv).float()
               k_gene += (
                      perturbation_size * torch.randn(k_gene.size(), device=k_gene.device) * diffs
               )  # random adding noise only to diff places
              # k_gene = torch.clamp(k_gene, min_val, max_val)
              # interp_mask = binom_sampler.sample()
              # k_gene = interp_mask * original_lv + (1 - interp_mask) * k_gene
               k_pop.append(k_gene)
               # print("Size of k_pop:", len(k_pop))

           # Combine parent_pop and k_pop for the next generation
           now_pop = parent_pop + k_pop
           prev_best = now_best
           # Apply a final clamp across all now_pop before next iteration starts
           now_pop = [torch.clamp(tensor, min=min_val, max=max_val) for tensor in now_pop]
      

      
     # After the loop, save the last image if it exists
       if best_image_tensor is not None:
          tensor_image = best_image_tensor.to(device)
          tensor_image = tensor_image.unsqueeze(0).to(device)
          tensor_image_np= tensor_image.squeeze().detach().cpu().numpy()
          perturb_logit = classifier(tensor_image).squeeze().detach().cpu().numpy()
          perturb_label = np.argmax(perturb_logit).item()
          predicted_labels.append(perturb_label)
          all_img_lst.append(tensor_image_np)
          # Save the image as a numpy array
          image_filename = f'image_{saved_images-1}_iteration{g_idx + 1}_X{original_label}_Y{perturb_label}'
          # Save the image as a numpy array
          np.save(os.path.join(proj_path,'generated_images', image_filename), tensor_image_np)
       else: 
          print("image is none")
    else: 
       # IF no match, simply skip to the next iteration (no image is saved or processed)
       print(f"Label mismatch for image {saved_images}:expected {expected_label},got {original_label}")
   
# Save the images as a numpy array
all_imgs = np.vstack(all_img_lst)
np.save(os.path.join(proj_path, "bound_imgs_svhn_sd.npy"), all_imgs)
